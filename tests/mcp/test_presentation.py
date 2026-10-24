from __future__ import annotations

import pytest
from mcp.types import ImageContent, TextContent

from loommux.execution.events import PresentationFailure, PresentationImage, PresentationText
from loommux.mcp.presentation import format_binding, format_output_token_limit_notice, format_tool_result_text
from loommux.mcp.result import ImageDeliveryLimits, make_tool_result


def test_completed_result_projects_only_ipython_visible_output() -> None:
    result = {"ok": True, "execution": 5, "status": "completed", "result_text": "42", "output_text": "Out[5]: 42\n", "output_omitted": False}
    printed = {"ok": True, "execution": 6, "status": "completed", "result_text": "", "output_text": "printed\n", "output_omitted": False}
    silent = {"ok": True, "execution": 7, "status": "completed", "result_text": "", "output_text": "", "output_omitted": False}

    assert format_tool_result_text("run_cell", result) == "In [5]:\nOut[5]: 42\n"
    assert format_tool_result_text("run_cell", printed) == "In [6]:\nprinted\n"
    assert format_tool_result_text("run_cell", silent) == "In [7]:"


@pytest.mark.parametrize(
    ("elapsed", "first_line"),
    [
        (0.0004, "In [9] · 0 ms:"),
        (0.2704, "In [9] · 270 ms:"),
        (0.9996, "In [9] · 1.0 s:"),
        (54.34, "In [9] · 54.3 s:"),
        (1263.44, "In [9] · 1263.4 s:"),
    ],
)
def test_the_first_line_shows_the_duration_in_milliseconds_below_a_second_and_seconds_above(elapsed: float, first_line: str) -> None:
    # S10, on both rendering paths: plain text and rich content.
    status = {"ok": True, "execution": 9, "status": "completed", "result_text": "", "output_text": "done\n", "output_omitted": False, "elapsed_seconds": elapsed}
    assert format_tool_result_text("wait", status) == f"{first_line}\ndone\n"
    rich = make_tool_result("run_cell", {**status, "_presentation": (PresentationText("done\n"),)}, "content")
    assert isinstance(rich.content[0], TextContent) and rich.content[0].text == f"{first_line}\n"


def test_execution_status_says_how_long_the_execution_ran() -> None:
    status = {"ok": True, "execution": 7, "status": "completed", "elapsed_seconds": 54.3387, "kernel_pid": 9}
    assert format_tool_result_text("execution_status", status).splitlines()[:2] == ["execution 7: completed", "elapsed: 54.3 s"]


def test_execution_states_name_the_integer_coordinate() -> None:
    running = {"ok": True, "execution": 5, "status": "running", "output_omitted_reason": "running"}
    large = {
        "ok": True,
        "execution": 5,
        "status": "completed",
        "output_omitted_reason": "token_limit_exceeded",
        "output_total_lines": 301,
        "output_total_characters": 2_600,
        "output_total_utf8_bytes": 2_600,
    }
    error = {"ok": False, "execution": 5, "status": "error", "error": {"ename": "ZeroDivisionError", "evalue": "division by zero"}}
    killed = {"ok": False, "execution": 5, "status": "killed"}

    assert format_tool_result_text("run_cell", running) == "In [5]:\nRunning: use wait() for completion, read_output() for collected output, or search_output() to locate text."
    assert format_tool_result_text("wait", large) == f"In [5]:\n{format_output_token_limit_notice(large)}"
    assert format_tool_result_text("run_cell", error) == "In [5]:\nError: ZeroDivisionError: division by zero"
    assert format_tool_result_text("wait", killed) == "In [5]:\nKilled: restart() stopped this execution."


def test_token_limited_notice_reports_size_and_recovery_paths() -> None:
    status = {
        "output_total_lines": 301,
        "output_total_characters": 1_023,
        "output_total_utf8_bytes": 1_023,
    }

    notice = format_output_token_limit_notice(status)
    assert "301 lines" in notice
    assert "1,023 characters" in notice
    assert "1,023 B;" in notice
    assert "read_output()" in notice
    assert "search_output()" in notice
    assert "%%loommux --full-output" in notice

    status["output_total_utf8_bytes"] = 1_024
    assert "1 KiB;" in format_output_token_limit_notice(status)


def test_marked_terminal_execution_renders_its_complete_combined_output() -> None:
    error = {
        "ok": False,
        "execution": 5,
        "status": "error",
        "output_text": "before failure\nTraceback...\n",
        "output_omitted": False,
    }
    killed = {
        "ok": False,
        "execution": 6,
        "status": "killed",
        "output_text": "before reset\n",
        "output_omitted": False,
    }

    assert format_tool_result_text("run_cell", error) == "In [5]:\nbefore failure\nTraceback...\n"
    assert format_tool_result_text("wait", killed) == "In [6]:\nbefore reset\nKilled: restart() stopped this execution."


def test_unmarked_error_with_a_small_combined_body_preserves_its_traceback() -> None:
    error = {
        "ok": False,
        "execution": 5,
        "status": "error",
        "error": {"ename": "RuntimeError", "evalue": "expected failure"},
        "output_text": "before failure\nTraceback...\n",
        "output_omitted": False,
    }

    assert format_tool_result_text("run_cell", error) == "In [5]:\nbefore failure\nTraceback...\n"


def test_read_search_and_status_surfaces_are_output_oriented() -> None:
    assert format_tool_result_text("read_output", {"ok": True, "returned_lines": 1, "text": "1 | payload"}) == "1 | payload"
    assert format_tool_result_text("read_output", {"ok": True, "returned_lines": 0, "text": ""}) == "No output lines are available."
    assert format_tool_result_text("search_output", {"ok": True, "matched_lines": 0}) == "No matching output lines were found."
    assert format_tool_result_text("status", {"ok": True, "kernel_started": True, "busy": False, "recent_execution": 5, "workspace": "/tmp/ws", "workspace_resolution": "launch_cwd"}) == "kernel: idle\nrecent_execution: 5\nworkspace: /tmp/ws\nworkspace_resolution: launch_cwd"
    assert format_tool_result_text("status", {"ok": True, "kernel_started": False, "kernel_restarting": True, "busy": False, "recent_execution": 5}).startswith("kernel: restarting\n")
    assert format_tool_result_text("status", {"ok": True, "kernel_name": "lab", "kernel_started": True, "busy": False, "recent_execution": 5}).startswith("name: lab\nkernel: idle\n")


@pytest.mark.parametrize(
    ("binding", "text"),
    [
        ({"name": "private-1a2b3c4d", "kind": "opened", "executions": 0}, "Kernel: private-1a2b3c4d (opened)"),
        ({"name": "task", "kind": "attached", "executions": 12}, "Kernel: task (attached; 12 earlier executions)"),
        ({"name": "task", "kind": "attached", "executions": 1, "released": "private-1a2b3c4d"}, "Kernel: task (attached; 1 earlier execution; let go of private-1a2b3c4d)"),
    ],
)
def test_a_binding_change_names_the_kernel_and_how_it_was_chosen(binding: dict, text: str) -> None:
    assert format_binding(binding) == text


def test_a_running_result_says_why_it_returned_and_where_the_output_stands() -> None:
    status = {"ok": True, "execution": 7, "status": "running", "silent_seconds": 30.4, "recent_lines": ["✓ 41 passed (12.3s)", "waiting for http://localhost:5173 …"]}

    assert format_tool_result_text("run_cell", status) == (
        "In [7]:\nRunning, silent for 30 s: use wait() for completion, read_output() for collected output, or search_output() to locate text.\n"
        "Last lines:\n  ✓ 41 passed (12.3s)\n  waiting for http://localhost:5173 …"
    )


def envelope(*lines: str) -> str:
    """What the environment says in one result: one block that starts a line and is closed."""
    return "\n<loommux-notice>\n" + "\n".join(lines) + "\n</loommux-notice>"


def test_the_binding_line_follows_the_output_and_precedes_a_lease_alert_in_one_envelope() -> None:
    result = make_tool_result(
        "run_cell",
        {"ok": True, "status": "completed", "execution": 1, "output_text": "42\n", "binding": {"name": "lab", "kind": "opened", "executions": 0}, "lease_alert": {"standing": "unbound", "fallback_window_seconds": 30}},
        "content",
    )

    texts = [block.text for block in result.content]
    assert len(texts) == 2
    assert texts[0] == "In [1]:\n42\n"
    assert texts[1].startswith("\n<loommux-notice>\nKernel: lab (opened)\nLease alert (unbound)")
    assert texts[1].endswith("\n</loommux-notice>")
    assert format_tool_result_text("execution_status", {"ok": True, "execution": 5, "status": "completed", "output_total_lines": 2}).startswith("execution 5: completed")


def test_notices_follow_even_an_omitted_output_and_precede_the_binding_and_alert_in_the_envelope() -> None:
    notices = [
        {"execution": None, "text": "00-vitals.ipy failed: NameError: x"},
        {"execution": 3, "text": "📬 reply"},
        {"execution": 4, "text": "⚠ memory"},
    ]
    result = make_tool_result(
        "run_cell",
        {
            "ok": True,
            "status": "completed",
            "execution": 4,
            "output_omitted_reason": "token_limit_exceeded",
            "output_total_lines": 301,
            "output_total_characters": 60_000,
            "output_total_utf8_bytes": 60_000,
            "notices": notices,
            "binding": {"name": "lab", "kind": "opened", "executions": 0},
            "lease_alert": {"standing": "unbound", "fallback_window_seconds": 30},
        },
        "content",
    )

    texts = [block.text for block in result.content]
    assert len(texts) == 2
    assert texts[0].startswith("In [4]:\nOutput omitted")
    # The shown execution's own notice needs no label; others name their source.
    lines = texts[1].split("\n")
    assert lines[:6] == ["", "<loommux-notice>", "Startup · 00-vitals.ipy failed: NameError: x", "In [3] · 📬 reply", "⚠ memory", "Kernel: lab (opened)"]
    assert lines[6].startswith("Lease alert (unbound)")
    assert lines[7:] == ["</loommux-notice>"]


@pytest.mark.parametrize(
    "status",
    [
        {"output_text": "42"},
        {"output_omitted_reason": "token_limit_exceeded", "output_total_lines": 1, "output_total_characters": 60_000, "output_total_utf8_bytes": 60_000},
    ],
)
def test_the_envelope_starts_a_line_after_output_that_does_not_end_one(status: dict) -> None:
    # A client joins a result's blocks end to end; this is what its reader sees.
    result = make_tool_result("run_cell", {"ok": True, "status": "completed", "execution": 4, "notices": [{"execution": 4, "text": "⚠ memory"}], **status}, "content")

    joined = "".join(block.text for block in result.content)
    assert joined.endswith(envelope("⚠ memory"))
    assert joined.splitlines()[-3:] == ["<loommux-notice>", "⚠ memory", "</loommux-notice>"]


def test_a_result_with_nothing_to_say_has_no_envelope() -> None:
    result = make_tool_result("run_cell", {"ok": True, "status": "completed", "execution": 1, "output_text": "42\n"}, "content")

    assert [block.text for block in result.content] == ["In [1]:\n42\n"]


def test_a_result_without_an_execution_labels_every_notice() -> None:
    result = make_tool_result("status", {"ok": True, "kernel_started": True, "notices": [{"execution": 2, "text": "done"}]}, "content")

    assert result.content[-1].text == envelope("In [2] · done")


def test_tool_failure_surface_remains_concise() -> None:
    assert format_tool_result_text("read_output", {"ok": False, "status": "invalid_stream", "message": "bad stream"}) == "invalid_stream: bad stream"


def test_presentation_handles_remaining_lifecycle_and_status_cases() -> None:
    assert format_tool_result_text("interrupt", {"ok": True, "status": "interrupt_sent", "execution": 7}) == "Interrupt sent to Python execution 7."
    assert format_tool_result_text("interrupt", {"ok": True, "status": "idle"}) == "Python kernel is idle."
    assert format_tool_result_text("restart", {"ok": True, "status": "restarted"}).startswith("Python kernel restarted")
    assert format_tool_result_text("execution_status", {"ok": False, "execution": 8, "status": "error", "error": {"ename": "NameError"}}).startswith("execution 8: error\nPython execution 8 failed with NameError")


def test_rich_execution_content_preserves_text_image_text_order_and_detail() -> None:
    result = make_tool_result(
        "run_cell",
        {
            "ok": True,
            "execution": 12,
            "status": "completed",
            "_presentation": (
                PresentationText("before image\n"),
                PresentationImage("eA==", "image/png", "low", 1),
                PresentationText("after image\n"),
                PresentationImage("eQ==", "image/jpeg", None, 2),
            ),
        },
        "structured",
    )

    assert [block.type for block in result.content] == ["text", "text", "image", "text", "image"]
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "In [12]:\n"
    assert isinstance(result.content[2], ImageContent)
    assert result.content[2].meta == {"detail": "low", "execution": 12, "display_ordinal": 1}
    assert isinstance(result.content[4], ImageContent)
    assert result.content[4].meta == {"detail": "original", "execution": 12, "display_ordinal": 2}
    assert result.structured_content is not None
    assert "_presentation" not in result.structured_content


def test_lease_alert_closes_plain_and_rich_results() -> None:
    alert = {"standing": "unbound", "fallback_window_seconds": 4800.0}
    plain = make_tool_result("status", {"ok": True, "kernel_started": True, "lease_alert": alert}, "structured")
    rich = make_tool_result(
        "run_cell",
        {"ok": True, "execution": 3, "status": "completed", "_presentation": (PresentationText("done\n"),), "lease_alert": alert},
        "content",
    )

    assert [block.type for block in plain.content] == ["text", "text"]
    assert isinstance(plain.content[0], TextContent)
    assert plain.content[0].text.startswith("kernel: idle")
    assert plain.structured_content is not None
    assert plain.structured_content["lease_alert"] == alert
    assert [block.text for block in rich.content if isinstance(block, TextContent)][:2] == ["In [3]:\n", "done\n"]
    for result in (plain, rich):
        last = result.content[-1]
        assert isinstance(last, TextContent)
        assert last.text.startswith("\n<loommux-notice>\nLease alert (unbound): this MCP session never opened the standalone GET stream")
        assert "4,800 idle seconds" in last.text


def test_lease_alert_names_a_lost_stream() -> None:
    result = make_tool_result("status", {"ok": True, "lease_alert": {"standing": "stream_lost", "fallback_window_seconds": 86400}}, "content")

    last = result.content[-1]
    assert isinstance(last, TextContent)
    assert "closed and was not reopened" in last.text
    assert "86,400 idle seconds" in last.text


def test_lease_alert_keeps_a_fractional_window() -> None:
    result = make_tool_result("status", {"ok": True, "lease_alert": {"standing": "unbound", "fallback_window_seconds": 90.5}}, "content")

    last = result.content[-1]
    assert isinstance(last, TextContent)
    assert "expires after 90.5 idle seconds" in last.text


def test_rich_execution_content_keeps_neighbors_when_an_image_is_rejected() -> None:
    result = make_tool_result(
        "wait",
        {
            "ok": True,
            "execution": 3,
            "status": "completed",
            "_presentation": (
                PresentationText("before\n"),
                PresentationImage("not-base64", "image/png", "original", 2),
                PresentationText("after\n"),
                PresentationImage("eA==", "image/png", "wrong", 3),
            ),
        },
        "content",
    )

    assert [block.type for block in result.content] == ["text", "text", "text", "text", "text"]
    assert [block.text for block in result.content if isinstance(block, TextContent)] == [
        "In [3]:\n",
        "before\n",
        "Image delivery failed for execution 3 display 2: invalid Base64 image data.",
        "after\n",
        "Image delivery failed for execution 3 display 3: detail must be low, high, or original; received 'wrong'.",
    ]


def test_rich_execution_content_enforces_image_delivery_limits() -> None:
    result = make_tool_result(
        "run_cell",
        {
            "ok": True,
            "execution": 4,
            "status": "completed",
            "_presentation": (PresentationImage("eA==", "image/png", None, 1),),
        },
        "content",
        ImageDeliveryLimits(max_image_bytes=0, max_images=1, max_total_image_bytes=1),
    )

    assert len(result.content) == 2
    assert isinstance(result.content[1], TextContent)
    assert "0-byte limit" in result.content[1].text


def test_rich_execution_keeps_images_but_omits_token_limited_text() -> None:
    result = make_tool_result(
        "run_cell",
        {
            "ok": True,
            "execution": 5,
            "status": "completed",
            "output_omitted_reason": "token_limit_exceeded",
            "output_total_lines": 301,
            "output_total_characters": 2_600,
            "output_total_utf8_bytes": 2_600,
            "_presentation": (
                PresentationText("\n".join(f"line-{number}" for number in range(301))),
                PresentationImage("eA==", "image/png", None, 1),
                PresentationText("after image"),
            ),
        },
        "content",
    )

    assert [block.type for block in result.content] == ["text", "text", "image"]
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "In [5]:\n"
    assert isinstance(result.content[1], TextContent)
    assert result.content[1].text == format_output_token_limit_notice(
        {
            "output_total_lines": 301,
            "output_total_characters": 2_600,
            "output_total_utf8_bytes": 2_600,
        }
    )
    assert "read_output() to read all lines or search_output()" in result.content[1].text
    assert "line-0" not in result.content[1].text


def test_rich_execution_rejects_malformed_gif_data() -> None:
    result = make_tool_result(
        "run_cell",
        {
            "ok": True,
            "execution": 6,
            "status": "completed",
            "_presentation": (PresentationImage("eA==", "image/gif", None, 1),),
        },
        "content",
    )

    assert len(result.content) == 2
    assert isinstance(result.content[1], TextContent)
    assert "invalid GIF data" in result.content[1].text


def test_rich_execution_keeps_explicit_failures_and_rejects_invalid_image_shapes() -> None:
    result = make_tool_result(
        "run_cell",
        {
            "ok": True,
            "execution": 7,
            "status": "completed",
            "_presentation": (
                PresentationFailure("Image delivery failed for execution 7 display 1: source unavailable."),
                PresentationImage("eA==", "image/svg+xml", None, 2),
                PresentationImage(b"x", "image/png", None, 3),
            ),
        },
        "content",
    )

    assert [block.text for block in result.content if isinstance(block, TextContent)] == [
        "In [7]:\n",
        "Image delivery failed for execution 7 display 1: source unavailable.",
        "Image delivery failed for execution 7 display 2: unsupported MIME type image/svg+xml.",
        "Image delivery failed for execution 7 display 3: image data must be Base64 text.",
    ]


def test_rich_execution_enforces_image_count_and_total_byte_limits() -> None:
    images = (PresentationImage("eA==", "image/png", None, 1), PresentationImage("eQ==", "image/png", None, 2))
    count_limited = make_tool_result(
        "run_cell",
        {"ok": True, "execution": 8, "status": "completed", "_presentation": images},
        "content",
        ImageDeliveryLimits(max_image_bytes=1, max_images=1, max_total_image_bytes=2),
    )
    total_limited = make_tool_result(
        "run_cell",
        {"ok": True, "execution": 8, "status": "completed", "_presentation": images},
        "content",
        ImageDeliveryLimits(max_image_bytes=1, max_images=2, max_total_image_bytes=1),
    )

    assert isinstance(count_limited.content[2], TextContent)
    assert "1-image limit" in count_limited.content[2].text
    assert isinstance(total_limited.content[2], TextContent)
    assert "total image bytes exceed the 1-byte limit" in total_limited.content[2].text


def test_rich_execution_accepts_a_single_frame_gif() -> None:
    result = make_tool_result(
        "wait",
        {
            "ok": True,
            "execution": 9,
            "status": "completed",
            "_presentation": (PresentationImage("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==", "image/gif", None, 1),),
        },
        "content",
    )

    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "In [9]:\n"
    assert isinstance(result.content[1], ImageContent)
    assert result.content[1].mimeType == "image/gif"


def test_make_tool_result_rejects_an_unknown_result_mode() -> None:
    with pytest.raises(ValueError, match="unknown result mode"):
        make_tool_result("status", {"ok": True}, "unknown")  # type: ignore[arg-type]
