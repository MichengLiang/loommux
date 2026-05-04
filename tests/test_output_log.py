from __future__ import annotations

from loommux.output_log import ExecutionLogs, LineLog, parse_output_log_handle


def test_line_log_reports_invalid_read_parameters_and_empty_ranges() -> None:
    log = LineLog()
    log.append("alpha\nbeta\n")

    invalid_range = log.read("bad")
    invalid_endpoint = log.read("x:2")
    invalid_max_chars = log.read(max_chars=0)
    empty = log.read("3:2")

    assert invalid_range["status"] == "invalid_line_range"
    assert invalid_endpoint["status"] == "invalid_line_range"
    assert invalid_max_chars["status"] == "invalid_max_chars"
    assert empty["ok"] is True
    assert empty["returned_lines"] == 0
    assert empty["text"] == ""


def test_line_log_search_handles_modes_context_and_errors() -> None:
    log = LineLog()
    log.append("alpha\nbeta-match\ngamma\nDELTA-MATCH\n")

    ignore_case = log.search("delta-match", query_mode="literal", ignore_case=True)
    clipped = log.search("match", query_mode="literal", max_chars=5)
    auto_fallback = log.search("[", query_mode="auto")
    invalid_regex = log.search("[", query_mode="regex")
    invalid_context = log.search("match", context_before=-1)

    assert ignore_case["matched_lines"] == 1
    assert "M 4 | DELTA-MATCH" in str(ignore_case["text"])
    assert "M 2 | beta-...[5 chars omitted]" in str(clipped["text"])
    assert auto_fallback["query_interpretation"] == "literal"
    assert invalid_regex["status"] == "invalid_query"
    assert invalid_context["status"] == "invalid_context"


def test_execution_logs_write_result_traceback_and_parse_handles() -> None:
    logs = ExecutionLogs("exec-000123")

    logs.append_stdout("hello\n")
    logs.append_stderr("warn\n")
    logs.append_result("42", 7)
    logs.append_traceback(["Traceback line", "ValueError: bad"])

    assert logs.handles["combined"] == "python-output:exec-000123"
    assert logs.handles["stderr"] == "python-output:exec-000123/stderr"
    assert logs.result.read()["text"] == "42"
    assert "Out[7]: 42" in str(logs.combined.read()["text"])
    assert "ValueError: bad" in str(logs.traceback.read()["text"])
    bad_scheme = parse_output_log_handle("bad")
    missing_execution = parse_output_log_handle("python-output:")
    bad_stream = parse_output_log_handle("python-output:exec-000123/bad")

    assert parse_output_log_handle("python-output:exec-000123/stderr") == ("exec-000123", "stderr")
    assert isinstance(bad_scheme, dict)
    assert bad_scheme["status"] == "invalid_output_log"
    assert isinstance(missing_execution, dict)
    assert missing_execution["status"] == "invalid_output_log"
    assert isinstance(bad_stream, dict)
    assert bad_stream["status"] == "invalid_output_log"
