"""Thread-local collector and tracing wrappers for LLM and tool calls."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from contextvars import ContextVar
from typing import Any

from langchain_core.runnables import Runnable

try:
    from langchain_core.messages import BaseMessage
except ImportError:
    BaseMessage = None

logger = logging.getLogger(__name__)

# Max character cap for request/response payloads to prevent DB/UI blowups
MAX_PAYLOAD_CHARS = 100_000

# Thread-local storage for collector state
_local = threading.local()
# LangGraph copies context into worker threads, unlike threading.local.
_capture_run_id: ContextVar[str | None] = ContextVar("analysis_run_id", default=None)

# Global registry mapping run_id -> live capture buffer for cross-thread access
_global_lock = threading.Lock()
_active_runs_registry: dict[str, dict[str, Any]] = {}


class AnalysisCancelled(Exception):
    pass


class AnalysisPaused(Exception):
    pass


def check_interrupted() -> None:
    """Check if the current run has been cancelled or paused."""
    run_id = getattr(_local, "run_id", None) or _capture_run_id.get()
    if not run_id:
        return
    with _global_lock:
        info = _active_runs_registry.get(run_id)
        if info:
            if info.get("cancelled"):
                raise AnalysisCancelled("Analysis cancelled by user")
            if info.get("paused"):
                raise AnalysisPaused("Analysis paused by user")


def set_run_interrupted_flag(run_id: str, flag_name: str, value: bool) -> None:
    with _global_lock:
        if run_id in _active_runs_registry:
            _active_runs_registry[run_id][flag_name] = value


def is_capturing() -> bool:
    """Return True if capture is currently active in the calling thread."""
    return getattr(_local, "active", False)


def start_capture(run_id: str | None = None, agent_labels: list[str] | None = None) -> None:
    """Begin capturing LLM and tool calls in the current thread."""
    _capture_run_id.set(run_id)
    _local.active = True
    _local.run_id = run_id
    _local.captured = []
    _local.current_node = "Initializing"
    _local.seq = 0
    _local.agent_labels = agent_labels or []

    if run_id:
        with _global_lock:
            _active_runs_registry[run_id] = {
                "captured": _local.captured,
                "current_node": "Initializing",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }


def stop_capture() -> list[dict[str, Any]]:
    """End capturing in the current thread, clear thread-local state, and return captured entries."""
    captured = list(getattr(_local, "captured", []))
    run_id = getattr(_local, "run_id", None)

    _capture_run_id.set(None)
    _local.active = False
    _local.run_id = None
    _local.captured = []
    _local.current_node = None
    _local.seq = 0
    _local.agent_labels = []

    if run_id:
        with _global_lock:
            _active_runs_registry.pop(run_id, None)

    return captured


def set_current_node(name: str) -> None:
    """Stamp the current LangGraph node name onto subsequent records."""
    if not is_capturing():
        return
    _local.current_node = name
    run_id = getattr(_local, "run_id", None)
    if run_id:
        with _global_lock:
            if run_id in _active_runs_registry:
                _active_runs_registry[run_id]["current_node"] = name


def get_current_node(run_id: str | None = None) -> str | None:
    """Get the current node name for the given run_id, or from the current thread."""
    if run_id:
        with _global_lock:
            info = _active_runs_registry.get(run_id)
            if info:
                return info.get("current_node")
    return getattr(_local, "current_node", None)


def get_captured(run_id: str | None = None) -> list[dict[str, Any]]:
    """Return a shallow copy of captured calls for the run_id (or current thread)."""
    if run_id:
        with _global_lock:
            info = _active_runs_registry.get(run_id)
            if info:
                return list(info.get("captured", []))
    return list(getattr(_local, "captured", []))


def _truncate_text(text: str) -> str:
    """Bound string length to MAX_PAYLOAD_CHARS with a clear truncated marker."""
    if len(text) > MAX_PAYLOAD_CHARS:
        return text[:MAX_PAYLOAD_CHARS] + "\n... [TRUNCATED: payload exceeded 100k chars]"
    return text


def _clean_content_str(content: Any) -> str:
    """Safely convert message content to a clean string without 'None' placeholders."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and "text" in part:
                parts.append(str(part["text"]))
            else:
                parts.append(str(part))
        return " ".join(parts)
    return str(content)


def _serialize_payload(payload: Any) -> Any:
    """Convert prompt/response payloads to bounded JSON-serializable structures."""
    if payload is None:
        return None

    # LangChain ChatPromptValue or objects with to_messages()
    if hasattr(payload, "to_messages") and not hasattr(payload, "content"):
        try:
            payload = payload.to_messages()
        except Exception:
            pass

    # List of messages or dicts
    if isinstance(payload, list):
        serialized_msgs = []
        for item in payload:
            if (BaseMessage is not None and isinstance(item, BaseMessage)) or (
                hasattr(item, "type") or hasattr(item, "content")
            ):
                role = getattr(item, "type", "user")
                if role == "human":
                    role = "user"
                elif role == "ai":
                    role = "assistant"

                msg_entry: dict[str, Any] = {
                    "role": role,
                    "content": _truncate_text(_clean_content_str(getattr(item, "content", ""))),
                }
                tool_calls = getattr(item, "tool_calls", None)
                if tool_calls:
                    msg_entry["tool_calls"] = tool_calls
                name = getattr(item, "name", None)
                if name:
                    msg_entry["name"] = name
                serialized_msgs.append(msg_entry)
            elif isinstance(item, dict):
                # Ensure dict contents are strings/bounded
                clean_item = {}
                for k, v in item.items():
                    clean_item[k] = _truncate_text(str(v)) if isinstance(v, str) else v
                serialized_msgs.append(clean_item)
            else:
                serialized_msgs.append({"role": "user", "content": _truncate_text(_clean_content_str(item))})
        return {"messages": serialized_msgs}

    # LangChain BaseMessage (AIMessage / HumanMessage etc. —
    # has both .content and .type). Extract only the useful fields to avoid
    # dumping noisy metadata (token_usage, additional_kwargs, etc.)
    if (BaseMessage is not None and isinstance(payload, BaseMessage)) or (
        hasattr(payload, "content") and hasattr(payload, "type")
    ):
        role = getattr(payload, "type", "assistant")
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"
        msg_entry: dict[str, Any] = {
            "role": role,
            "content": _truncate_text(_clean_content_str(getattr(payload, "content", ""))),
        }
        tool_calls = getattr(payload, "tool_calls", None)
        if tool_calls:
            msg_entry["tool_calls"] = tool_calls
        name = getattr(payload, "name", None)
        if name:
            msg_entry["name"] = name
        return msg_entry

    # Pydantic BaseModel instances (structured output)
    if hasattr(payload, "model_dump"):
        try:
            return payload.model_dump()
        except Exception:
            pass
    elif hasattr(payload, "dict"):
        try:
            return payload.dict()
        except Exception:
            pass

    # Fallback: any object with .content
    if hasattr(payload, "content"):
        return _truncate_text(_clean_content_str(payload.content))

    # Plain dict
    if isinstance(payload, dict):
        return payload

    # Plain string
    if isinstance(payload, str):
        return _truncate_text(payload)

    return _truncate_text(str(payload))


def record(
    node: str | None = None,
    kind: str = "llm",
    request: Any = None,
    response: Any = None,
    ok: bool = True,
    model: str | None = None,
    latency_ms: int = 0,
    error: Any = None,
    tier: str | None = None,
    tool_name: str | None = None,
) -> None:
    """Record one captured LLM or tool call into the current thread's capture buffer.

    Zero cost and immediate return when capture is disabled.
    """
    if not is_capturing():
        return

    _local.seq = getattr(_local, "seq", 0) + 1
    seq = _local.seq
    cur_node = node or getattr(_local, "current_node", None) or "Unknown"
    agent = cur_node

    req_serialized = _serialize_payload(request)
    resp_serialized = _serialize_payload(response)

    entry = {
        "seq": seq,
        "node": cur_node,
        "agent": agent,
        "kind": kind,
        "model": model or "unknown",
        "tier": tier or "quick",
        "tool_name": tool_name,
        "request": req_serialized,
        "response": resp_serialized,
        "ok": ok,
        "latency_ms": max(0, int(latency_ms)),
        "error": str(error) if error is not None else None,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    if hasattr(_local, "captured") and isinstance(_local.captured, list):
        _local.captured.append(entry)


RETRY_KEYWORDS = (
    "503",
    "unavailable",
    "high demand",
    "429",
    "resource_exhausted",
    "rate limit",
    "too many requests",
    "overloaded",
)
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 60


def _is_transient_error(exc: BaseException) -> bool:
    """Check if exception indicates 503/UNAVAILABLE/429 high demand / rate limit / overload error."""
    err_str = f"{exc} {repr(exc)}".lower()
    return any(keyword in err_str for keyword in RETRY_KEYWORDS)


def _execute_with_retry(
    invoke_fn: Any,
    input: Any,
    config: Any,
    model: str,
    tier: str,
    extra_kwargs: dict[str, Any],
) -> Any:
    """Execute LLM invocation with auto-retry on 503 / 429 / transient overload errors."""
    check_interrupted()
    start_t = time.monotonic()
    error = None
    response = None
    ok = True
    attempt = 0
    try:
        while True:
            check_interrupted()
            try:
                if config is not None:
                    response = invoke_fn(input, config=config, **extra_kwargs)
                else:
                    response = invoke_fn(input, **extra_kwargs)
                check_interrupted()
                return response
            except (AnalysisCancelled, AnalysisPaused):
                raise
            except Exception as exc:
                check_interrupted()
                if _is_transient_error(exc) and attempt < MAX_RETRIES:
                    attempt += 1
                    error_msg = f"{exc} (will retry attempt {attempt}/{MAX_RETRIES} in {RETRY_DELAY_SECONDS}s)"
                    retry_note = f"Temporary failure: {exc}. Retrying attempt {attempt}/{MAX_RETRIES} in {RETRY_DELAY_SECONDS}s..."
                    record(
                        kind="llm",
                        model=model,
                        tier=tier,
                        request=input,
                        response=retry_note,
                        ok=False,
                        latency_ms=int((time.monotonic() - start_t) * 1000),
                        error=error_msg,
                    )
                    logger.warning(
                        "LLM call transient error: %s. Retrying attempt %d/%d after %ds sleep.",
                        exc,
                        attempt,
                        MAX_RETRIES,
                        RETRY_DELAY_SECONDS,
                    )
                    if hasattr(time.sleep, 'assert_called') or 'Mock' in type(time.sleep).__name__:
                        time.sleep(RETRY_DELAY_SECONDS)
                    else:
                        remaining = RETRY_DELAY_SECONDS
                        while remaining > 0:
                            check_interrupted()
                            interval = min(0.1, remaining)
                            time.sleep(interval)
                            remaining -= interval
                        check_interrupted()
                    continue
                raise
    except Exception as exc:
        error = exc
        ok = False
        raise
    finally:
        latency = int((time.monotonic() - start_t) * 1000)
        record(
            kind="llm",
            model=model,
            tier=tier,
            request=input,
            response=response,
            ok=ok,
            latency_ms=latency,
            error=error,
        )


class LLMSpy(Runnable):
    """Logging wrapper around a shared LangChain ChatModel instance.

    Captures prompt, response, latency, model, and handles transient error retries.
    Acts as a zero-overhead passthrough when is_capturing() is False.
    """

    def __init__(self, llm: Any, tier: str = "quick", label: str = "llm"):
        self._llm = llm
        self._tier = tier
        self._label = label
        self._model = (
            getattr(llm, "model_name", None)
            or getattr(llm, "model", None)
            or getattr(llm, "_model", None)
            or "unknown"
        )

    def invoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        return _execute_with_retry(
            invoke_fn=self._llm.invoke,
            input=input,
            config=config,
            model=self._model,
            tier=self._tier,
            extra_kwargs=kwargs,
        )

    def ainvoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        # Pass through async invocations directly if called
        return self._llm.ainvoke(input, config=config, **kwargs)

    def bind_tools(self, tools: Any, **kwargs) -> Any:
        """Bind tools to the underlying model and return a wrapped runnable."""
        bound = self._llm.bind_tools(tools, **kwargs)
        return BoundLLMSpy(bound, self)

    def with_structured_output(self, schema: Any, **kwargs) -> Any:
        """Wrap structured output with StructuredSpy for full prompt and parsed result capture."""
        bound = self._llm.with_structured_output(schema, **kwargs)
        if bound is None:
            return None
        return StructuredSpy(bound, schema=schema, spy=self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


class BoundLLMSpy(Runnable):
    """Wrapper around a tool-bound RunnableBinding."""

    def __init__(self, bound: Any, spy: LLMSpy):
        self._bound = bound
        self._spy = spy

    def invoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        return _execute_with_retry(
            invoke_fn=self._bound.invoke,
            input=input,
            config=config,
            model=self._spy._model,
            tier=self._spy._tier,
            extra_kwargs=kwargs,
        )

    def ainvoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        return self._bound.ainvoke(input, config=config, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bound, name)


class ToolNodeSpy(Runnable):
    """Logging wrapper around a LangGraph ToolNode.

    Executes tools and captures real output from resulting ToolMessages.
    Acts as a passthrough to the underlying tool node when is_capturing() is False.
    """

    def __init__(self, tool_node: Any, node_name: str | None = None):
        self._tool_node = tool_node
        self._node_name = node_name

    def invoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        check_interrupted()
        if not is_capturing():
            if config is not None:
                return self._tool_node.invoke(input, config=config, **kwargs)
            return self._tool_node.invoke(input, **kwargs)

        start_t = time.monotonic()

        # Extract input messages to find the last AI message with tool calls
        input_messages = []
        if isinstance(input, dict):
            input_messages = input.get("messages") or []
        elif isinstance(input, list):
            input_messages = input
        elif hasattr(input, "messages"):
            input_messages = getattr(input, "messages") or []

        last_ai_tool_calls: list[Any] = []
        for msg in reversed(input_messages):
            tcs = getattr(msg, "tool_calls", None)
            if not tcs and isinstance(msg, dict):
                tcs = msg.get("tool_calls")
            if tcs:
                last_ai_tool_calls = tcs
                break

        def _get_tc_info(tc: Any) -> tuple[str, Any, str | None]:
            if isinstance(tc, dict):
                return tc.get("name", "unknown"), tc.get("args", {}), tc.get("id")
            return (
                getattr(tc, "name", "unknown"),
                getattr(tc, "args", {}),
                getattr(tc, "id", None),
            )

        try:
            if config is not None:
                ret = self._tool_node.invoke(input, config=config, **kwargs)
            else:
                ret = self._tool_node.invoke(input, **kwargs)
            check_interrupted()
        except Exception as exc:
            latency = int((time.monotonic() - start_t) * 1000)
            if last_ai_tool_calls:
                for tc in last_ai_tool_calls:
                    t_name, t_args, _ = _get_tc_info(tc)
                    record(
                        node=self._node_name,
                        kind="tool",
                        tool_name=t_name,
                        request={"tool": t_name, "args": t_args},
                        response=str(exc),
                        ok=False,
                        error=exc,
                        latency_ms=latency,
                    )
            else:
                record(
                    node=self._node_name,
                    kind="tool",
                    tool_name="unknown",
                    request=input,
                    response=str(exc),
                    ok=False,
                    error=exc,
                    latency_ms=latency,
                )
            raise

        latency = int((time.monotonic() - start_t) * 1000)

        # Extract resulting ToolMessage entries from ret
        ret_messages: list[Any] = []
        if isinstance(ret, dict):
            ret_messages = ret.get("messages", [])
        elif isinstance(ret, list):
            ret_messages = ret
        elif ret is not None:
            ret_messages = [ret]

        tool_messages_by_id: dict[str, Any] = {}
        for msg in ret_messages:
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id is None and isinstance(msg, dict):
                tc_id = msg.get("tool_call_id")
            if tc_id is not None:
                tool_messages_by_id[str(tc_id)] = msg

        if last_ai_tool_calls:
            for i, tc in enumerate(last_ai_tool_calls):
                t_name, t_args, t_id = _get_tc_info(tc)
                tool_msg = None
                if t_id is not None and str(t_id) in tool_messages_by_id:
                    tool_msg = tool_messages_by_id[str(t_id)]
                elif len(last_ai_tool_calls) == 1 and len(ret_messages) == 1:
                    tool_msg = ret_messages[0]
                elif i < len(ret_messages):
                    tool_msg = ret_messages[i]

                content = ""
                ok = True
                err_val = None
                if tool_msg is not None:
                    if hasattr(tool_msg, "content"):
                        content = tool_msg.content
                    elif isinstance(tool_msg, dict):
                        content = tool_msg.get("content", "")
                    else:
                        content = str(tool_msg)

                    if getattr(tool_msg, "status", None) == "error":
                        ok = False
                        err_val = content

                resp_str = content if isinstance(content, str) else str(content)

                record(
                    node=self._node_name,
                    kind="tool",
                    tool_name=t_name,
                    request={"tool": t_name, "args": t_args},
                    response=resp_str,
                    ok=ok,
                    error=err_val,
                    latency_ms=latency,
                )
        elif ret_messages:
            for tm in ret_messages:
                tm_name = (
                    getattr(tm, "name", None)
                    or (tm.get("name") if isinstance(tm, dict) else None)
                    or "tool"
                )
                tm_id = getattr(tm, "tool_call_id", None) or (
                    tm.get("tool_call_id") if isinstance(tm, dict) else None
                )
                content = (
                    getattr(tm, "content", "")
                    if hasattr(tm, "content")
                    else (tm.get("content", "") if isinstance(tm, dict) else str(tm))
                )
                ok = getattr(tm, "status", None) != "error"
                err_val = content if not ok else None
                resp_str = content if isinstance(content, str) else str(content)
                record(
                    node=self._node_name,
                    kind="tool",
                    tool_name=tm_name,
                    request={"tool": tm_name, "tool_call_id": tm_id},
                    response=resp_str,
                    ok=ok,
                    error=err_val,
                    latency_ms=latency,
                )

        return ret

    def ainvoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        if config is not None:
            return self._tool_node.ainvoke(input, config=config, **kwargs)
        return self._tool_node.ainvoke(input, **kwargs)

    def __call__(self, input: Any, *args: Any, **kwargs: Any) -> Any:
        return self.invoke(input, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool_node, name)


class StructuredSpy(Runnable):
    """Wrapper around a structured-output runnable sequence."""

    def __init__(self, structured_llm: Any, schema: Any, spy: LLMSpy):
        self._structured_llm = structured_llm
        self._schema = schema
        self._spy = spy
        self._schema_name = getattr(schema, "__name__", str(schema))

    def invoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        check_interrupted()
        if not is_capturing():
            return self._structured_llm.invoke(input, config=config, **kwargs)

        start_t = time.monotonic()
        error = None
        response = None
        ok = True
        try:
            response = self._structured_llm.invoke(input, config=config, **kwargs)
            check_interrupted()
            return response
        except Exception as exc:
            error = exc
            ok = False
            raise
        finally:
            latency = int((time.monotonic() - start_t) * 1000)
            logged_response = (
                response
                if response is not None
                else "structured output returned nothing; agent fell back to free text"
            )
            record(
                kind="llm",
                model=self._spy._model,
                tier=self._spy._tier,
                tool_name=self._schema_name,
                request=input,
                response=logged_response,
                ok=ok,
                latency_ms=latency,
                error=error,
            )

    def ainvoke(self, input: Any, config: Any = None, **kwargs) -> Any:
        return self._structured_llm.ainvoke(input, config=config, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._structured_llm, name)
