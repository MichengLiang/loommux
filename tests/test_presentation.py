from __future__ import annotations

import pytest
from mcp.types import ImageContent, TextContent

from loommux.execution import PresentationFailure, PresentationImage, PresentationText
from loommux.mcp_result_policy import ImageDeliveryLimits, make_tool_result
from loommux.presentation import format_tool_result_text


def test_completed_result_projects_only_ipython_visible_output() -> None:
    result = {"ok": True, "execution": 5, "status": "completed", "result_text": "42", "output_text": "Out[5]: 42\n", "output_omitted": False}
    printed = {"ok": True, "execution": 6, "status": "completed", "result_text": "", "output_text": "printed\n", "output_omitted": False}
    silent = {"ok": True, "execution": 7, "status": "completed", "result_text": "", "output_text": "", "output_omitted": False}

    assert format_tool_result_text("run_python", result) == "In [5]:\nOut[5]: 42\n"
    assert format_tool_result_text("run_python", printed) == "In [6]:\nprinted\n"
    assert format_tool_result_text("run_python", silent) == "In [7]:"


def test_execution_states_name_the_integer_coordinate() -> None:
    running = {"ok": True, "execution": 5, "status": "running", "output_omitted_reason": "running"}
    large = {"ok": True, "execution": 5, "status": "completed", "output_omitted_reason": "line_limit_exceeded", "output_line_limit": 300}
    error = {"ok": False, "execution": 5, "status": "error", "error": {"ename": "ZeroDivisionError", "evalue": "division by zero"}}
    killed = {"ok": False, "execution": 5, "status": "killed"}

    assert format_tool_result_text("run_python", running) == "In [5]:\nRunning: use wait_python() for completion, read_python_output() for collected output, or search_python_output() to locate text."
    assert format_tool_result_text("wait_python", large) == "In [5]:\nOutput: more than 300 lines; use read_python_output() to read all lines or search_python_output() to locate text."
    assert format_tool_result_text("run_python", error) == "In [5]:\nError: ZeroDivisionError: division by zero"
    assert format_tool_result_text("wait_python", killed) == "In [5]:\nKilled: reset_python() stopped this execution."


def test_marked_terminal_execution_renders_its_complete_combined_output() -> None:
    error = {
        "ok": False,
        "execution": 5,
        "status": "error",
        "full_output_requested": True,
        "output_text": "before failure\nTraceback...\n",
        "output_omitted": False,
    }
    killed = {
        "ok": False,
        "execution": 6,
        "status": "killed",
        "full_output_requested": True,
        "output_text": "before reset\n",
        "output_omitted": False,
    }

    assert format_tool_result_text("run_python", error) == "In [5]:\nbefore failure\nTraceback...\n"
    assert format_tool_result_text("wait_python", killed) == "In [6]:\nbefore reset\nKilled: reset_python() stopped this execution."


def test_unmarked_error_with_a_small_combined_body_preserves_its_traceback() -> None:
    error = {
        "ok": False,
        "execution": 5,
        "status": "error",
        "error": {"ename": "RuntimeError", "evalue": "expected failure"},
        "output_text": "before failure\nTraceback...\n",
        "output_omitted": False,
    }

    assert format_tool_result_text("run_python", error) == "In [5]:\nbefore failure\nTraceback...\n"


def test_read_search_and_status_surfaces_are_output_oriented() -> None:
    assert format_tool_result_text("read_python_output", {"ok": True, "returned_lines": 1, "text": "1 | payload"}) == "1 | payload"
    assert format_tool_result_text("read_python_output", {"ok": True, "returned_lines": 0, "text": ""}) == "No output lines are available."
    assert format_tool_result_text("search_python_output", {"ok": True, "matched_lines": 0}) == "No matching output lines were found."
    assert format_tool_result_text("python_status", {"ok": True, "kernel_started": True, "busy": False, "recent_execution": 5, "workspace": "/tmp/ws", "workspace_resolution": "launch_cwd"}) == "kernel: idle\nrecent_execution: 5\nworkspace: /tmp/ws\nworkspace_resolution: launch_cwd"
    assert format_tool_result_text("python_execution_status", {"ok": True, "execution": 5, "status": "completed", "output_total_lines": 2}).startswith("execution 5: completed")


def test_tool_failure_surface_remains_concise() -> None:
    assert format_tool_result_text("read_python_output", {"ok": False, "status": "invalid_stream", "message": "bad stream"}) == "invalid_stream: bad stream"


def test_presentation_handles_remaining_lifecycle_and_status_cases() -> None:
    assert format_tool_result_text("interrupt_python", {"ok": True, "status": "interrupt_sent", "execution": 7}) == "Interrupt sent to Python execution 7."
    assert format_tool_result_text("interrupt_python", {"ok": True, "status": "idle"}) == "Python kernel is idle."
    assert format_tool_result_text("reset_python", {"ok": True, "status": "restarted"}).startswith("Python kernel restarted")
    assert format_tool_result_text("python_execution_status", {"ok": False, "execution": 8, "status": "error", "error": {"ename": "NameError"}}).startswith("execution 8: error\nPython execution 8 failed with NameError")


def test_rich_execution_content_preserves_text_image_text_order_and_detail() -> None:
    result = make_tool_result(
        "run_python",
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
        "dual_channel",
    )

    assert [block.type for block in result.content] == ["text", "image", "text", "image"]
    assert isinstance(result.content[1], ImageContent)
    assert result.content[1].meta == {"detail": "low", "execution": 12, "display_ordinal": 1}
    assert isinstance(result.content[3], ImageContent)
    assert result.content[3].meta == {"detail": "high", "execution": 12, "display_ordinal": 2}
    assert result.structured_content is not None
    assert "_presentation" not in result.structured_content


def test_rich_execution_content_keeps_neighbors_when_an_image_is_rejected() -> None:
    result = make_tool_result(
        "wait_python",
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
        "content_only",
    )

    assert [block.type for block in result.content] == ["text", "text", "text", "text"]
    assert [block.text for block in result.content if isinstance(block, TextContent)] == [
        "before\n",
        "Image delivery failed for execution 3 display 2: invalid Base64 image data.",
        "after\n",
        "Image delivery failed for execution 3 display 3: detail must be low, high, or original; received 'wrong'.",
    ]


def test_rich_execution_content_enforces_image_delivery_limits() -> None:
    result = make_tool_result(
        "run_python",
        {
            "ok": True,
            "execution": 4,
            "status": "completed",
            "_presentation": (PresentationImage("eA==", "image/png", None, 1),),
        },
        "content_only",
        ImageDeliveryLimits(max_image_bytes=0, max_images=1, max_total_image_bytes=1),
    )

    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert "0-byte limit" in result.content[0].text


def test_rich_execution_keeps_images_but_omits_line_limited_text() -> None:
    result = make_tool_result(
        "run_python",
        {
            "ok": True,
            "execution": 5,
            "status": "completed",
            "output_omitted_reason": "line_limit_exceeded",
            "output_line_limit": 300,
            "_presentation": (
                PresentationText("\n".join(f"line-{number}" for number in range(301))),
                PresentationImage("eA==", "image/png", None, 1),
                PresentationText("after image"),
            ),
        },
        "content_only",
    )

    assert [block.type for block in result.content] == ["text", "image"]
    assert isinstance(result.content[0], TextContent)
    assert "exceeds 300 lines" in result.content[0].text
    assert "read_python_output() to read all lines or search_python_output()" in result.content[0].text
    assert "line-0" not in result.content[0].text


def test_rich_execution_rejects_malformed_gif_data() -> None:
    result = make_tool_result(
        "run_python",
        {
            "ok": True,
            "execution": 6,
            "status": "completed",
            "_presentation": (PresentationImage("eA==", "image/gif", None, 1),),
        },
        "content_only",
    )

    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert "invalid GIF data" in result.content[0].text


def test_rich_execution_keeps_explicit_failures_and_rejects_invalid_image_shapes() -> None:
    result = make_tool_result(
        "run_python",
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
        "content_only",
    )

    assert [block.text for block in result.content if isinstance(block, TextContent)] == [
        "Image delivery failed for execution 7 display 1: source unavailable.",
        "Image delivery failed for execution 7 display 2: unsupported MIME type image/svg+xml.",
        "Image delivery failed for execution 7 display 3: image data must be Base64 text.",
    ]


def test_rich_execution_enforces_image_count_and_total_byte_limits() -> None:
    images = (PresentationImage("eA==", "image/png", None, 1), PresentationImage("eQ==", "image/png", None, 2))
    count_limited = make_tool_result(
        "run_python",
        {"ok": True, "execution": 8, "status": "completed", "_presentation": images},
        "content_only",
        ImageDeliveryLimits(max_image_bytes=1, max_images=1, max_total_image_bytes=2),
    )
    total_limited = make_tool_result(
        "run_python",
        {"ok": True, "execution": 8, "status": "completed", "_presentation": images},
        "content_only",
        ImageDeliveryLimits(max_image_bytes=1, max_images=2, max_total_image_bytes=1),
    )

    assert isinstance(count_limited.content[1], TextContent)
    assert "1-image limit" in count_limited.content[1].text
    assert isinstance(total_limited.content[1], TextContent)
    assert "total image bytes exceed the 1-byte limit" in total_limited.content[1].text


def test_rich_execution_accepts_a_single_frame_gif() -> None:
    result = make_tool_result(
        "wait_python",
        {
            "ok": True,
            "execution": 9,
            "status": "completed",
            "_presentation": (PresentationImage("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==", "image/gif", None, 1),),
        },
        "content_only",
    )

    assert isinstance(result.content[0], ImageContent)
    assert result.content[0].mimeType == "image/gif"


def test_make_tool_result_rejects_an_unknown_result_policy() -> None:
    with pytest.raises(ValueError, match="unknown result channel policy"):
        make_tool_result("python_status", {"ok": True}, "unknown")  # type: ignore[arg-type]
