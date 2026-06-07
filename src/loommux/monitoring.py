from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any, Protocol

DEFAULT_MONITOR_URL = "http://127.0.0.1:9765/api/events"
DEFAULT_MAX_QUEUE_EVENTS = 1000
DEFAULT_MAX_TEXT_FIELD_CHARS = 64_000
DEFAULT_TIMEOUT_SECONDS = 0.25
DEFAULT_RETRY_DELAY_SECONDS = 0.25

MonitorEvent = dict[str, Any]
MonitorSender = Callable[[str, Mapping[str, Any], float], None]
type MonitorScalar = str | int | float | bool | None
type SanitizedValue = MonitorScalar | list[SanitizedValue] | dict[str, SanitizedValue]


class MonitorPublisher(Protocol):
    def publish(self, event: Mapping[str, Any]) -> None:
        """Queue or discard a monitor event without raising into the caller path."""

    def close(self) -> None:
        """Release any publisher resources."""


class NoopMonitorPublisher:
    def publish(self, event: Mapping[str, Any]) -> None:
        del event

    def close(self) -> None:
        pass


class BackgroundMonitorPublisher:
    def __init__(
        self,
        url: str = DEFAULT_MONITOR_URL,
        *,
        max_queue_events: int = DEFAULT_MAX_QUEUE_EVENTS,
        max_text_field_chars: int = DEFAULT_MAX_TEXT_FIELD_CHARS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        sender: MonitorSender | None = None,
    ) -> None:
        if max_queue_events <= 0:
            raise ValueError("max_queue_events must be greater than 0")
        if max_text_field_chars <= 0:
            raise ValueError("max_text_field_chars must be greater than 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be greater than 0")
        self.url = url
        self.max_queue_events = max_queue_events
        self.max_text_field_chars = max_text_field_chars
        self.timeout_seconds = timeout_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self.dropped_count = 0
        self._sender = sender or _post_json
        self._queue: deque[MonitorEvent] = deque()
        self._condition = threading.Condition()
        self._close_requested = threading.Event()
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="loommux-monitor-publisher", daemon=True)
        self._worker.start()

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def queued_count(self) -> int:
        with self._condition:
            return len(self._queue)

    def publish(self, event: Mapping[str, Any]) -> None:
        sanitized = _sanitize_event(event, self.max_text_field_chars)
        with self._condition:
            if self._closed:
                return
            if len(self._queue) >= self.max_queue_events:
                self._queue.popleft()
                self.dropped_count += 1
            self._queue.append(sanitized)
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._close_requested.set()
            self._condition.notify_all()
        if self._worker is not threading.current_thread():
            self._worker.join(timeout=1)

    def snapshot_queue(self) -> list[MonitorEvent]:
        with self._condition:
            return [dict(event) for event in self._queue]

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                event = self._queue.popleft()
            try:
                self._sender(self.url, event, self.timeout_seconds)
            except Exception:
                # Monitor delivery is an observation side path. Network failures must not
                # leak into MCP stdout/stderr or change tool behavior.
                self._requeue_failed_event(event)
                self._wait_before_retry()

    def _requeue_failed_event(self, event: MonitorEvent) -> None:
        with self._condition:
            if self._closed:
                return
            if len(self._queue) >= self.max_queue_events:
                self.dropped_count += 1
                return
            self._queue.appendleft(event)

    def _wait_before_retry(self) -> None:
        self._close_requested.wait(timeout=self.retry_delay_seconds)


def create_monitor_publisher() -> MonitorPublisher:
    if os.environ.get("LOOMMUX_MONITOR_DISABLED") == "1":
        return NoopMonitorPublisher()
    return BackgroundMonitorPublisher(os.environ.get("LOOMMUX_MONITOR_URL", DEFAULT_MONITOR_URL))


def _sanitize_event(value: Mapping[str, Any], max_text_field_chars: int) -> MonitorEvent:
    return {str(key): _sanitize_value(item, max_text_field_chars) for key, item in value.items()}


def _sanitize_value(value: object, max_text_field_chars: int) -> SanitizedValue:
    if isinstance(value, str):
        return _truncate_text(value, max_text_field_chars)
    if isinstance(value, Mapping):
        return {str(key): _sanitize_value(item, max_text_field_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, max_text_field_chars) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item, max_text_field_chars) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return _truncate_text(str(value), max_text_field_chars)


def _truncate_text(value: str, max_text_field_chars: int) -> str:
    if len(value) <= max_text_field_chars:
        return value
    truncated = len(value) - max_text_field_chars
    return f"{value[:max_text_field_chars]}...[{truncated} chars truncated]"


def _post_json(url: str, event: Mapping[str, Any], timeout_seconds: float) -> None:
    data = json.dumps(event, ensure_ascii=False, default=str).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read()
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        raise
