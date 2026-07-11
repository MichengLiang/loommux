from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

import pytest

from loommux.monitoring import (
    DEFAULT_MONITOR_URL,
    BackgroundMonitorPublisher,
    NoopMonitorPublisher,
    create_monitor_publisher,
    run_monitored_tool_call,
    safe_publish,
)


def test_disabled_environment_returns_noop_publisher(monkeypatch) -> None:
    monkeypatch.setenv("LOOMMUX_MONITOR_DISABLED", "1")

    publisher = create_monitor_publisher()

    try:
        assert isinstance(publisher, NoopMonitorPublisher)
        publisher.publish({"type": "tool_call_started"})
        publisher.close()
        publisher.close()
    finally:
        publisher.close()


def test_default_url_and_environment_override(monkeypatch) -> None:
    monkeypatch.delenv("LOOMMUX_MONITOR_DISABLED", raising=False)
    monkeypatch.delenv("LOOMMUX_MONITOR_URL", raising=False)

    default_publisher = create_monitor_publisher()
    try:
        assert isinstance(default_publisher, BackgroundMonitorPublisher)
        assert default_publisher.url == DEFAULT_MONITOR_URL
        assert DEFAULT_MONITOR_URL == "http://127.0.0.1:9765/api/events"
    finally:
        default_publisher.close()

    monkeypatch.setenv("LOOMMUX_MONITOR_URL", "http://127.0.0.1:9999/custom")
    override_publisher = create_monitor_publisher()
    try:
        assert isinstance(override_publisher, BackgroundMonitorPublisher)
        assert override_publisher.url == "http://127.0.0.1:9999/custom"
    finally:
        override_publisher.close()


def test_publish_returns_quickly_when_backend_is_absent() -> None:
    publisher = BackgroundMonitorPublisher("http://127.0.0.1:1/api/events", max_queue_events=10, timeout_seconds=0.01)

    try:
        started = time.perf_counter()
        for index in range(100):
            publisher.publish({"type": "tool_call_started", "index": index})
        elapsed = time.perf_counter() - started

        assert elapsed < 0.05
    finally:
        publisher.close()


def test_queue_overflow_is_bounded_and_counts_dropped_events() -> None:
    def never_send(_url: str, _event: Mapping[str, Any], _timeout_seconds: float) -> None:
        time.sleep(0.2)

    publisher = BackgroundMonitorPublisher("http://127.0.0.1:1/api/events", max_queue_events=3, timeout_seconds=0.01, sender=never_send)

    try:
        for index in range(20):
            publisher.publish({"type": "execution_output", "text": "x" * 200, "index": index})

        assert publisher.queued_count <= 3
        assert publisher.dropped_count > 0
        assert publisher.max_queue_events == 3
    finally:
        publisher.close()


def test_failed_event_is_retried_and_delivered_after_sender_recovers() -> None:
    attempts = 0
    sent: list[Mapping[str, Any]] = []

    def fail_once_then_send(_url: str, event: Mapping[str, Any], _timeout_seconds: float) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("backend unavailable")
        sent.append(event)

    publisher = BackgroundMonitorPublisher("http://127.0.0.1:1/api/events", max_queue_events=5, retry_delay_seconds=0.01, sender=fail_once_then_send)

    try:
        publisher.publish({"type": "tool_call_started", "call_id": "call-1"})
        deadline = time.monotonic() + 1
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)

        assert attempts >= 2
        assert sent == [{"type": "tool_call_started", "call_id": "call-1"}]
        assert publisher.queued_count == 0
    finally:
        publisher.close()


def test_capacity_pressure_can_displace_old_failed_event_for_recent_events() -> None:
    processing_old = threading.Event()
    allow_old_failure = threading.Event()

    def fail_old_event(_url: str, event: Mapping[str, Any], _timeout_seconds: float) -> None:
        event_id = str(event["id"])
        if event_id == "old":
            processing_old.set()
            assert allow_old_failure.wait(timeout=1)
            raise OSError("backend unavailable for old event")

    publisher = BackgroundMonitorPublisher("http://127.0.0.1:1/api/events", max_queue_events=3, retry_delay_seconds=1, sender=fail_old_event)

    try:
        publisher.publish({"type": "tool_call_started", "id": "old"})
        assert processing_old.wait(timeout=1)

        for event_id in ("new-1", "new-2", "new-3"):
            publisher.publish({"type": "tool_call_started", "id": event_id})

        allow_old_failure.set()
        deadline = time.monotonic() + 1
        while publisher.dropped_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        queued_ids = [str(event["id"]) for event in publisher.snapshot_queue()]
        assert queued_ids == ["new-1", "new-2", "new-3"]
        assert publisher.dropped_count >= 1
    finally:
        publisher.close()


def test_publish_notifications_do_not_shorten_retry_delay() -> None:
    attempts = 0

    def always_fail(_url: str, _event: Mapping[str, Any], _timeout_seconds: float) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("backend unavailable")

    publisher = BackgroundMonitorPublisher("http://127.0.0.1:1/api/events", max_queue_events=20, retry_delay_seconds=0.2, sender=always_fail)

    try:
        publisher.publish({"type": "tool_call_started", "id": "old"})
        deadline = time.monotonic() + 1
        while attempts == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        for index in range(10):
            publisher.publish({"type": "tool_call_started", "id": f"new-{index}"})
            time.sleep(0.005)

        assert attempts == 1
    finally:
        publisher.close()


def test_publish_truncates_long_text_fields() -> None:
    sent: list[Mapping[str, Any]] = []

    def record_send(_url: str, event: Mapping[str, Any], _timeout_seconds: float) -> None:
        sent.append(event)

    publisher = BackgroundMonitorPublisher("http://127.0.0.1:1/api/events", max_queue_events=3, max_text_field_chars=8, sender=record_send)

    try:
        publisher.publish({"type": "execution_output", "payload": {"text": "abcdefghijk"}})
        deadline = time.monotonic() + 1
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)

        assert sent
        assert sent[-1]["payload"]["text"] == "abcdefgh...[3 chars truncated]"
    finally:
        publisher.close()


def test_close_is_idempotent_and_publish_after_close_is_noop() -> None:
    sent: list[Mapping[str, Any]] = []

    def record_send(_url: str, event: Mapping[str, Any], _timeout_seconds: float) -> None:
        sent.append(event)

    publisher = BackgroundMonitorPublisher("http://127.0.0.1:1/api/events", sender=record_send)
    publisher.publish({"type": "tool_call_started"})
    publisher.close()
    publisher.close()
    publisher.publish({"type": "tool_call_finished"})

    assert publisher.closed is True
    assert len(sent) <= 1


def test_monitored_call_records_success_and_exception_without_leaking_publish_errors() -> None:
    events: list[Mapping[str, Any]] = []

    class Publisher:
        def publish(self, event: Mapping[str, Any]) -> None:
            events.append(event)

        def close(self) -> None:
            pass

    publisher = Publisher()
    assert run_monitored_tool_call("python_status", {}, publisher, lambda _call_id: {"ok": True}) == {"ok": True}
    with pytest.raises(RuntimeError, match="boom"):
        run_monitored_tool_call("python_status", {}, publisher, lambda _call_id: (_ for _ in ()).throw(RuntimeError("boom")))

    assert [event["type"] for event in events] == ["tool_call_started", "tool_call_finished", "tool_call_started", "tool_call_finished"]
    assert events[-1]["status"] == "exception"

    class BrokenPublisher:
        def publish(self, event: Mapping[str, Any]) -> None:
            raise RuntimeError("monitor unavailable")

        def close(self) -> None:
            pass

    safe_publish(BrokenPublisher(), {"type": "execution_output", "items": (1, 2)})
