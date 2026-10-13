"""Image references and storage ports; binary data never enters history."""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import Field

from nemo.core.contracts.base import Contract

MAX_IMAGES = 8
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_REQUEST_IMAGE_BYTES = 20 * 1024 * 1024


class ImageRef(Contract):
    asset_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(gt=0, le=MAX_IMAGE_BYTES)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


@dataclass(frozen=True)
class ImageData:
    ref: ImageRef
    data: bytes = field(repr=False)


class ImageStore(Protocol):
    def put(self, session_id: str, data: bytes, mime_type: str) -> ImageRef: ...

    def get(self, session_id: str, asset_id: str) -> ImageData: ...
