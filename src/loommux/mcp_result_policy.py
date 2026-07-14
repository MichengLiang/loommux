from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent

from loommux.execution import PresentationFailure, PresentationImage, PresentationText
from loommux.presentation import format_tool_result_text

ResultChannelPolicy = Literal["dual_channel", "content_only"]


@dataclass(frozen=True)
class ImageDeliveryLimits:
    """Bounds protect one tool response from exhausting a model request payload."""

    max_image_bytes: int = 20 * 1024 * 1024
    max_images: int = 20
    max_total_image_bytes: int = 100 * 1024 * 1024


DEFAULT_IMAGE_DELIVERY_LIMITS = ImageDeliveryLimits()
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
ALLOWED_IMAGE_DETAILS = {"low", "high", "original"}


def make_tool_result(
    tool_name: str,
    raw_status: Mapping[str, Any],
    policy: ResultChannelPolicy,
    image_limits: ImageDeliveryLimits = DEFAULT_IMAGE_DELIVERY_LIMITS,
) -> ToolResult:
    status = {key: value for key, value in raw_status.items() if not key.startswith("_")}
    content = _rich_execution_content(tool_name, status, raw_status.get("_presentation"), image_limits)
    if content is None:
        content = format_tool_result_text(tool_name, status)
    if policy == "dual_channel":
        return ToolResult(content=content, structured_content=status)
    if policy == "content_only":
        return ToolResult(content=content)
    raise ValueError(f"unknown result channel policy: {policy}")


def _rich_execution_content(
    tool_name: str,
    status: Mapping[str, Any],
    presentation: object,
    limits: ImageDeliveryLimits,
) -> list[TextContent | ImageContent] | None:
    if tool_name not in {"run_python", "wait_python"}:
        return None
    if status.get("status") == "running" or not isinstance(presentation, tuple):
        return None

    execution = status.get("execution")
    if not isinstance(execution, int):
        return None
    content: list[TextContent | ImageContent] = []
    text_omitted = status.get("output_omitted_reason") == "line_limit_exceeded"
    if text_omitted:
        content.append(
            TextContent(
                type="text",
                text=(
                    f"Text output omitted because it exceeds {status.get('output_line_limit')} lines; "
                    "use read_python_output() to inspect it."
                ),
            )
        )
    accepted_images = 0
    accepted_bytes = 0
    for element in presentation:
        if isinstance(element, PresentationText):
            if not text_omitted:
                content.append(TextContent(type="text", text=element.text))
        elif isinstance(element, PresentationFailure):
            content.append(TextContent(type="text", text=element.message))
        elif isinstance(element, PresentationImage):
            image, error = _make_image_content(element, execution, accepted_images, accepted_bytes, limits)
            if image is None:
                content.append(TextContent(type="text", text=error))
            else:
                content.append(image)
                accepted_images += 1
                # _make_image_content accepts only str data before constructing an image.
                assert isinstance(element.data, str)
                accepted_bytes += len(base64.b64decode(element.data, validate=True))
    return content


def _make_image_content(
    element: PresentationImage,
    execution: int,
    accepted_images: int,
    accepted_bytes: int,
    limits: ImageDeliveryLimits,
) -> tuple[ImageContent | None, str]:
    location = f"execution {execution} display {element.display_ordinal}"
    if element.mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        return None, f"Image delivery failed for {location}: unsupported MIME type {element.mime_type}."
    if not isinstance(element.data, str):
        return None, f"Image delivery failed for {location}: image data must be Base64 text."
    try:
        decoded = base64.b64decode(element.data, validate=True)
    except (ValueError, TypeError):
        return None, f"Image delivery failed for {location}: invalid Base64 image data."
    detail = "high" if element.detail is None else element.detail
    if not isinstance(detail, str) or detail not in ALLOWED_IMAGE_DETAILS:
        return None, f"Image delivery failed for {location}: detail must be low, high, or original; received {detail!r}."
    if len(decoded) > limits.max_image_bytes:
        return None, f"Image delivery failed for {location}: image exceeds the {limits.max_image_bytes}-byte limit."
    if accepted_images >= limits.max_images:
        return None, f"Image delivery failed for {location}: image count exceeds the {limits.max_images}-image limit."
    if accepted_bytes + len(decoded) > limits.max_total_image_bytes:
        return None, f"Image delivery failed for {location}: total image bytes exceed the {limits.max_total_image_bytes}-byte limit."
    if element.mime_type == "image/gif":
        frame_count = _gif_frame_count(decoded)
        if frame_count is None:
            return None, f"Image delivery failed for {location}: invalid GIF data."
        if frame_count > 1:
            return None, f"Image delivery failed for {location}: animated GIF is not supported."
    return (
        ImageContent(
            type="image",
            data=element.data,
            mimeType=element.mime_type,
            _meta={"detail": detail, "execution": execution, "display_ordinal": element.display_ordinal},
        ),
        "",
    )


def _gif_frame_count(data: bytes) -> int | None:
    """Count GIF image descriptors without decoding or re-encoding user data."""
    if not data.startswith((b"GIF87a", b"GIF89a")) or len(data) < 13:
        return None
    position = 13
    if data[10] & 0x80:
        position += 3 * (2 ** ((data[10] & 0x07) + 1))
    if position > len(data):
        return None
    frames = 0
    while position < len(data):
        marker = data[position]
        position += 1
        if marker == 0x3B:
            return frames if frames else None
        if marker == 0x2C:
            frames += 1
            if position + 9 > len(data):
                return None
            packed = data[position + 8]
            position += 9
            if packed & 0x80:
                position += 3 * (2 ** ((packed & 0x07) + 1))
            if position >= len(data):
                return None
            position += 1
        elif marker == 0x21:
            if position >= len(data):
                return None
            position += 1
        else:
            return None
        while position < len(data):
            size = data[position]
            position += 1
            if size == 0:
                break
            position += size
            if position > len(data):
                return None
        else:
            return None
    return None
