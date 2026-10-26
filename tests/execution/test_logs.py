from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import loommux.execution.record as execution_module
from loommux.execution import Execution
from loommux.execution.logs import ExecutionLogs, LineLog


def test_line_log_reads_ranges_and_clipping() -> None:
    log = LineLog()
    log.append("alpha\nbeta\ngamma\n")

    assert log.read(":2")["text"] == "alpha\nbeta"
    assert log.read("-2:")["text"] == "beta\ngamma"
    assert log.read("3:3", max_chars=3)["text"] == "gam...[2 chars omitted]"
    assert log.read("bad")["status"] == "invalid_line_range"
    assert log.read(max_chars=0)["status"] == "invalid_max_chars"
    log.append("A中🙂\n")
    assert log.character_count == len("alpha\nbeta\ngamma\nA中🙂\n")
    assert log.utf8_byte_count == len("alpha\nbeta\ngamma\nA中🙂\n".encode())


def test_line_log_reads_slice_shaped_coordinates() -> None:
    """The five forms the tool description shows, plus the negative end it warns about."""
    log = LineLog()
    log.append("".join(f"line {number}\n" for number in range(1, 6)))

    assert log.read(":")["text"] == "line 1\nline 2\nline 3\nline 4\nline 5"
    assert log.read(":2")["text"] == "line 1\nline 2"
    assert log.read("-2:")["text"] == "line 4\nline 5"
    assert log.read("2:4")["text"] == "line 2\nline 3\nline 4"
    assert log.read("3:3")["text"] == "line 3"
    # A negative end counts line numbers from the tail and stays inclusive, unlike
    # a Python slice: ``:-3`` stops at the third-from-last line instead of dropping
    # the last three, and a wrong reading of it over-delivers without an error.
    assert log.read(":-3")["text"] == "line 1\nline 2\nline 3"
    assert log.read(":-1")["text"] == "line 1\nline 2\nline 3\nline 4\nline 5"


def test_line_log_appends_in_linear_time() -> None:
    log = LineLog()
    chunk = "x" * 1023 + "\n"
    started = time.perf_counter()
    for _ in range(20_000):
        log.append(chunk)
    assert log.line_count == 20_000
    # Rebuilding the string per append took about 18 s here; linear takes milliseconds.
    assert time.perf_counter() - started < 2


def test_text_since_follows_output_in_linear_time() -> None:
    log = LineLog()
    chunk = "x" * 1023 + "\n"
    offset = 0
    started = time.perf_counter()
    for index in range(8 * 1024):
        log.append(chunk)
        if index % 4 == 0:
            offset += len(log.text_since(offset))
    offset += len(log.text_since(offset))
    assert offset == log.character_count
    # Joining the whole log per follow took about 5 s here for these 8 MB.
    assert time.perf_counter() - started < 2


@pytest.mark.parametrize("join_after", [None, 0, 1, 2, 3])
def test_text_since_matches_the_text_at_every_offset(join_after: int | None) -> None:
    log = LineLog()
    chunks = ["ab", "", "c", "中🙂\n", "defg"]
    for index, chunk in enumerate(chunks):
        log.append(chunk)
        if index == join_after:
            _ = log.text
    text = "".join(chunks)

    assert [log.text_since(offset) for offset in range(len(text) + 2)] == [text[offset:] for offset in range(len(text) + 2)]
    assert log.text == text


def test_line_log_keeps_a_chunk_appended_between_a_readers_join_and_clear() -> None:
    # The unsafe window is the few bytecodes between a reader's join and its
    # clearing of the queue. Racing threads into it needs about a million reads
    # per hit, and each read joins the whole text, which a fair scheduler such as
    # Windows' turns quadratic. Instead the reader stops inside the window and the
    # writer appends there: with the lock it waits and its chunk lands after the
    # clear; without the lock (verified with a lock-free `text`) it is lost every
    # time.
    log = LineLog()
    log.append("a\n")
    in_window = threading.Event()
    appended = threading.Event()

    class PendingThatOpensTheWindow(list[str]):
        def clear(self) -> None:
            in_window.set()
            appended.wait(0.2)
            super().clear()

    log._pending = PendingThatOpensTheWindow(log._pending)

    def writer() -> None:
        in_window.wait()
        log.append("b\n")
        appended.set()

    thread = threading.Thread(target=writer)
    thread.start()
    first = log.text
    thread.join(timeout=5)

    assert first == "a\n"
    assert log.text == "a\nb\n"
    assert log.line_count == 2


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


def test_execution_tracks_error_interrupt_and_complete_snapshots() -> None:
    record = Execution(execution=9, kernel_pid=12)
    record.append_stdout("one\n")
    record.append_result_text("first")
    record.append_result_text("second")
    record.record_error({"ename": "KeyboardInterrupt", "evalue": "", "traceback": ["\x1b[31mtrace"]})
    record.interrupt_requested = True
    record.finish()

    assert record.status == "interrupted"
    assert record.result_text == "first\nsecond"
    assert record.logs.traceback.text == "trace\n"
    snapshot = record.snapshot()
    status_snapshot = record.status_snapshot()

    assert snapshot["output_omitted"] is False
    assert snapshot["output_omitted_reason"] is None
    assert snapshot["output_total_characters"] == len(record.logs.combined.text)
    assert snapshot["output_total_utf8_bytes"] == len(record.logs.combined.text.encode("utf-8"))
    assert status_snapshot["error"] == {"ename": "KeyboardInterrupt", "evalue": ""}
    assert status_snapshot["output_total_characters"] == snapshot["output_total_characters"]
    assert status_snapshot["output_total_utf8_bytes"] == snapshot["output_total_utf8_bytes"]


@pytest.mark.parametrize(("token_count", "omitted"), [(5_000, False), (5_001, True)])
def test_execution_applies_the_token_limit(
    monkeypatch: pytest.MonkeyPatch,
    token_count: int,
    omitted: bool,
) -> None:
    record = Execution(execution=3, kernel_pid=12)
    record.append_stdout("payload " * 20)
    record.finish()
    calls = 0

    def count_tokens(_text: str) -> int:
        nonlocal calls
        calls += 1
        return token_count

    monkeypatch.setattr(execution_module, "_count_output_tokens", count_tokens)

    snapshot = record.snapshot()
    status = record.status_snapshot()

    assert snapshot["output_omitted"] is omitted
    assert snapshot["output_omitted_reason"] == ("token_limit_exceeded" if omitted else None)
    assert status["output_omitted_reason"] == snapshot["output_omitted_reason"]
    assert {"output_total_tokens", "output_token_limit", "output_token_encoding"}.isdisjoint(snapshot)
    assert calls == 1


def test_token_heavy_single_line_output_is_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    record = Execution(execution=3, kernel_pid=12)
    record.append_stdout("one token-heavy line")
    record.finish()
    monkeypatch.setattr(execution_module, "_count_output_tokens", lambda _text: 5_001)

    snapshot = record.snapshot()

    assert snapshot["output_omitted"] is True
    assert snapshot["output_omitted_reason"] == "token_limit_exceeded"


def test_full_output_and_running_states_do_not_consult_the_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution_module, "_count_output_tokens", lambda _text: pytest.fail("tokenizer should not be called"))
    running = Execution(execution=1, kernel_pid=12)
    running.append_stdout(("payload " * 20 + "\n") * 301)
    marked = Execution(execution=2, kernel_pid=12, _full_output_requested=True)
    marked.append_stdout(("payload " * 20 + "\n") * 301)
    marked.finish()

    assert running.snapshot()["output_omitted_reason"] == "running"
    assert marked.snapshot()["output_omitted"] is False


def test_tokenizer_failure_is_not_replaced_by_another_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    record = Execution(execution=3, kernel_pid=12)
    record.append_stdout("payload")
    record.finish()

    def unavailable(_text: str) -> int:
        raise OSError("encoding data is unavailable")

    monkeypatch.setattr(execution_module, "_count_output_tokens", unavailable)

    with pytest.raises(OSError, match="encoding data is unavailable"):
        record.snapshot()


def test_o200k_counter_treats_special_token_shaped_output_as_ordinary_text() -> None:
    assert execution_module._count_output_tokens("<|endoftext|>\n<|endofprompt|>") > 0


def test_execution_normalizes_every_stream_projection_before_logging() -> None:
    record = Execution(execution=4, kernel_pid=12)

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


def test_shared_line_log_contract_vectors() -> None:
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/text_contract/cases.json").read_text())

    for case in fixture["line_log"]:
        log = LineLog()
        log.append(case["text"])
        for request in case["reads"]:
            result = log.read(request["line_range"], max_chars=request["max_chars"])
            for field in ("total_lines", "returned_lines", "omitted_before", "omitted_after"):
                assert result[field] == request[field], (case["name"], request, field)
            assert result["text"] == request["expected_text"]
        for request in case["searches"]:
            result = log.search(
                request["query"],
                query_mode=request["query_mode"],
                context_before=request["context_before"],
                context_after=request["context_after"],
                ignore_case=request["ignore_case"],
                max_chars=request["max_chars"],
            )
            for field in ("query_interpretation", "matched_lines", "matches", "total_lines"):
                assert result[field] == request[field], (case["name"], request, field)
            assert result["text"] == request["expected_text"]
