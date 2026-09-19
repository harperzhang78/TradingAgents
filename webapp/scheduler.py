"""Background scheduler for periodic watchlist analysis runs."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from webapp.db import get_setting, set_setting
from webapp.runner import runner

logger = logging.getLogger(__name__)


class AnalysisScheduler:
    """Daemon thread checking periodic analysis schedule against SQLite settings."""

    def __init__(self, check_interval_seconds: int = 30):
        self.check_interval_seconds = check_interval_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="analysis-scheduler", daemon=True)
        self._thread.start()
        logger.info("Analysis scheduler background thread started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Analysis scheduler stopped.")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check_and_run()
            except Exception as e:
                logger.error("Error in scheduler loop: %s", e)
            self._stop_event.wait(self.check_interval_seconds)

    def _check_and_run(self) -> None:
        enabled_str = get_setting("schedule_enabled", "false").strip().lower()
        if enabled_str not in ("true", "1", "yes", "on"):
            return

        interval_str = get_setting("schedule_interval_minutes", "1440").strip()
        try:
            interval_minutes = float(interval_str)
        except ValueError:
            interval_minutes = 1440.0

        if interval_minutes <= 0:
            return

        last_run_str = get_setting("last_scheduled_run", "")
        now = datetime.now(timezone.utc)
        should_run = False

        if not last_run_str:
            should_run = True
        else:
            try:
                last_run_time = datetime.fromisoformat(last_run_str)
                elapsed_minutes = (now - last_run_time).total_seconds() / 60.0
                if elapsed_minutes >= interval_minutes:
                    should_run = True
            except Exception:
                should_run = True

        if should_run:
            logger.info("Periodic schedule triggered. Running analysis on watchlist...")
            set_setting("last_scheduled_run", now.isoformat())
            runner.start_watchlist_analysis(trigger="scheduled")


scheduler = AnalysisScheduler()
