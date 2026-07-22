from __future__ import annotations

from loommux.execution import Execution
from loommux.output_log import ExecutionLogs, LineLog


def test_line_log_reads_ranges_and_clipping() -> None:
    log = LineLog()
    log.append("alpha\nbeta\ngamma\n")

    assert log.read(":2")["text"] == "alpha\nbeta"
    assert log.read("-2:")["text"] == "beta\ngamma"
    assert log.read("3:3", max_chars=3)["text"] == "gam...[2 chars omitted]"
    assert log.read("bad")["status"] == "invalid_line_range"
    assert log.read(max_chars=0)["status"] == "invalid_max_chars"


def test_line_log_search_supports_context_and_query_modes() -> None:
    log = LineLog()
    log.append("alpha\nbeta-match\ngamma\nDELTA-MATCH\n")

    result = log.search("match", query_mode="literal", context_before=1, context_after=0)
    assert result["text"] == "C 1 | alpha\nM 2 | beta-match"
    assert log.search("delta-match", query_mode="literal", ignore_case=True)["matched_lines"] == 1
    assert log.search("[", query_mode="auto")["query_interpretation"] == "literal"
    assert log.search("[", query_mode="regex")["status"] == "invalid_query"
    assert log.search("match", context_before=-1)["status"] == "invalid_context"


def test_execution_logs_keep_streams_and_author_public_execution_label() -> None:
    logs = ExecutionLogs()
    logs.append_stdout("hello\n")
    logs.append_stderr("warn\n")
    logs.append_result("42", 5)
    logs.append_traceback(["Traceback", "ValueError: bad"])

    assert logs.stdout.text == "hello\n"
    assert logs.result.text == "42\n"
    assert "Out[5]: 42" in logs.combined.text
    assert "ValueError: bad" in logs.traceback.text
    assert logs.get("unknown") is None


def test_execution_tracks_error_interrupt_and_omitted_snapshots() -> None:
    record = Execution(execution=9, code="x", kernel_pid=12)
    record.append_stdout("one\n")
    record.append_result_text("first")
    record.append_result_text("second")
    record.record_error({"ename": "KeyboardInterrupt", "evalue": "", "traceback": ["\x1b[31mtrace"]})
    record.interrupt_requested = True
    record.finish()

    assert record.status == "interrupted"
    assert record.result_text == "first\nsecond"
    assert record.logs.traceback.text == "trace\n"
    assert record.snapshot(1)["output_omitted_reason"] == "line_limit_exceeded"
    assert record.status_snapshot()["error"] == {"ename": "KeyboardInterrupt", "evalue": ""}


def test_execution_normalizes_every_stream_projection_before_logging() -> None:
    record = Execution(execution=4, code="x", kernel_pid=12)

    first_stdout = record.append_stdout("stdout \x1b[3")
    second_stdout = record.append_stdout("1mvisible\x1b[0m\n")
    stderr = record.append_stderr("\x1b]0;title\x07stderr\n")
    result = record.append_result_text("\x1b[35mresult\x1b[0m")
    traceback = record.record_error({"ename": "RuntimeError", "evalue": "\x1b[31mbad\x1b[0m", "traceback": ["\x1b[31mtrace", "back\x1b[0m"]})

    assert first_stdout == "stdout "
    assert second_stdout == "visible\n"
    assert stderr == "stderr\n"
    assert result == "result"
    assert traceback == "trace\nback\n"
    assert "\x1b" not in record.logs.combined.text
    assert record.error == {"ename": "RuntimeError", "evalue": "bad", "traceback": ["trace", "back"]}
