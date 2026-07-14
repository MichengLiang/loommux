from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeAlias

from loommux.presentation import format_tool_result_text

DEFAULT_MONITOR_URL = "http://127.0.0.1:9765/api/events"
DEFAULT_MAX_QUEUE_EVENTS = 1000
DEFAULT_MAX_TEXT_FIELD_CHARS = 64_000
DEFAULT_TIMEOUT_SECONDS = 0.25
DEFAULT_RETRY_DELAY_SECONDS = 0.25

MonitorEvent = dict[str, Any]
MonitorSender = Callable[[str, Mapping[str, Any], float], None]
MonitoredToolOperation = Callable[[str], dict[str, Any]]
MonitorScalar: TypeAlias = str | int | float | bool | None
SanitizedValue: TypeAlias = MonitorScalar | list["SanitizedValue"] | dict[str, "SanitizedValue"]


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


def run_monitored_tool_call(tool_name: str, arguments: Mapping[str, Any], publisher: MonitorPublisher, operation: MonitoredToolOperation) -> dict[str, Any]:
    call_id = f"call-{uuid.uuid4().hex}"
    started_at = time.time()
    _safe_publish(
        publisher,
        {
            "type": "tool_call_started",
            "call_id": call_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "timestamp": started_at,
        },
    )
    try:
        raw_status = operation(call_id)
    except Exception as exc:
        _safe_publish(
            publisher,
            {
                "type": "tool_call_finished",
                "call_id": call_id,
                "tool_name": tool_name,
                "duration_ms": _duration_ms(started_at),
                "ok": False,
                "status": "exception",
                "result_summary": f"{type(exc).__name__}: {exc}",
                "pretty_text_summary": "",
                "timestamp": time.time(),
            },
        )
        raise

    pretty_text = format_tool_result_text(tool_name, raw_status)
    _safe_publish(
        publisher,
        {
            "type": "tool_call_finished",
            "call_id": call_id,
            "tool_name": tool_name,
            "duration_ms": _duration_ms(started_at),
            "ok": raw_status.get("ok") is not False,
            "status": _status_value(raw_status),
            "result_summary": _summarize_mapping(raw_status),
            "pretty_text_summary": _truncate_text(pretty_text, 2_000),
            "timestamp": time.time(),
        },
    )
    return raw_status


def safe_publish(publisher: MonitorPublisher, event: Mapping[str, Any]) -> None:
    _safe_publish(publisher, event)


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


def _safe_publish(publisher: MonitorPublisher, event: Mapping[str, Any]) -> None:
    try:
        publisher.publish(event)
    except Exception:
        pass


def _duration_ms(started_at: float) -> float:
    return round((time.time() - started_at) * 1000, 3)


def _status_value(status: Mapping[str, Any]) -> str:
    value = status.get("status")
    if value is not None:
        return str(value)
    if status.get("ok") is True:
        return "ok"
    if status.get("ok") is False:
        return "error"
    return "unknown"


def _summarize_mapping(status: Mapping[str, Any], *, max_items: int = 8) -> str:
    parts: list[str] = []
    for index, (key, value) in enumerate(status.items()):
        if index >= max_items:
            parts.append("...")
            break
        parts.append(f"{key}={_summarize_value(value)}")
    return _truncate_text(", ".join(parts), 2_000)


def _summarize_value(value: object) -> str:
    if isinstance(value, str):
        return value if len(value) <= 80 else f"{value[:80]}..."
    if value is None or isinstance(value, bool | int | float):
        return str(value)
    if isinstance(value, Mapping):
        return f"<mapping {len(value)}>"
    if isinstance(value, list | tuple):
        return f"<sequence {len(value)}>"
    return str(value)
