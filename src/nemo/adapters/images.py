"""Session-scoped, content-addressed image storage."""

import hashlib
import io
import os
import re
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.images import ImageData, ImageRef, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS

_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def inspect_image(data: bytes, mime_type: str | None = None) -> ImageRef:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ToolFailure("Image must be between 1 byte and 10 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=list(_MIMES)) as image:
                width, height = image.size
                detected = _MIMES[image.format]
                if width * height > MAX_IMAGE_PIXELS:
                    raise ToolFailure("Image exceeds the 20 megapixel limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise ToolFailure("Animated images are not supported")
                image.verify()
            # verify() alone does not decode JPEG pixels or catch every truncation.
            with Image.open(io.BytesIO(data), formats=list(_MIMES)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ToolFailure("Invalid image; supported formats are PNG, JPEG and WebP") from None
    if mime_type is not None and mime_type != detected:
        raise ToolFailure("Image content does not match its Content-Type")
    return ImageRef(asset_id=hashlib.sha256(data).hexdigest(), mime_type=detected,
                    size_bytes=len(data), width=width, height=height)


class FileImageStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, session_id: str, asset_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", asset_id):
            raise ToolFailure("Invalid image reference")
        scope = hashlib.sha256(session_id.encode()).hexdigest()
        path = self.root / scope / asset_id
        if path.resolve() != path or not path.resolve().is_relative_to(self.root):
            raise ToolFailure("Image storage path is not allowed")
        return path

    def put(self, session_id: str, data: bytes, mime_type: str) -> ImageRef:
        ref = inspect_image(data, mime_type)
        path = self._path(session_id, ref.asset_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Atomic publication; only server-generated names reach the store.
            import tempfile
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = temporary.name
            os.replace(temporary_path, path)
        except OSError:
            raise ToolFailure("Could not save image") from None
        return ref

    def get(self, session_id: str, asset_id: str) -> ImageData:
        path = self._path(session_id, asset_id)
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            raise ToolFailure("Image is missing or inaccessible in this session") from None
        if hashlib.sha256(data).hexdigest() != asset_id:
            raise ToolFailure("Stored image failed its integrity check")
        return ImageData(inspect_image(data), data)
