"""Describe visible IOPub presentation events before transport projection.

These event values preserve the order in which text and display data arrived.
They carry no MCP types, because the same execution record can be inspected by
ordinary Python callers or rendered by a protocol-specific consumer later.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PresentationText:
    """A normalized visible text fragment at its IOPub arrival position."""

    text: str


@dataclass(frozen=True)
class PresentationImage:
    """One display-data image before consumer-specific validation."""

    data: object
    mime_type: str
    detail: object
    display_ordinal: int


@dataclass(frozen=True)
class PresentationFailure:
    """A diagnostic occupying the position of an undeliverable image."""

    message: str


type PresentationElement = PresentationText | PresentationImage | PresentationFailure
IMAGE_MIME_PREFERENCE = ("image/png", "image/jpeg", "image/webp", "image/gif")
