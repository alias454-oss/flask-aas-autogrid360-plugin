# app/plugins/autogrid360/services/media.py
"""Local image validation, normalization, and storage for AutoGrid360."""

import io
import logging
import os
import warnings
from pathlib import Path
from uuid import uuid4

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from app.plugins.autogrid360.services.settings import listing_images_path


logger = logging.getLogger(__name__)

DEFAULT_MAX_LISTING_IMAGES = 12
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 24_000_000
DEFAULT_MAX_UPLOAD_REQUEST_BYTES = 32 * 1024 * 1024
DISPLAY_MAX_SIZE = (1600, 1200)
THUMBNAIL_MAX_SIZE = (320, 240)
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class ImageUploadError(ValueError):
    """Raised when a submitted listing image is unsafe or unsupported."""


def max_listing_images() -> int:
    """Return the configured maximum number of images per listing."""

    value = int(
        current_app.config.get(
            "AUTOGRID360_MAX_LISTING_IMAGES",
            DEFAULT_MAX_LISTING_IMAGES,
        )
    )
    return max(value, 1)


def max_image_bytes() -> int:
    """Return the configured maximum size for one uploaded source image."""

    value = int(
        current_app.config.get(
            "AUTOGRID360_MAX_IMAGE_BYTES",
            DEFAULT_MAX_IMAGE_BYTES,
        )
    )
    return max(value, 1)




def max_image_pixels() -> int:
    """Return the maximum decoded pixel count accepted for one source image."""

    value = int(
        current_app.config.get(
            "AUTOGRID360_MAX_IMAGE_PIXELS",
            DEFAULT_MAX_IMAGE_PIXELS,
        )
    )
    return max(value, 1)


def max_upload_request_bytes() -> int:
    """Return the maximum total multipart request size for an image upload."""

    value = int(
        current_app.config.get(
            "AUTOGRID360_MAX_UPLOAD_REQUEST_BYTES",
            DEFAULT_MAX_UPLOAD_REQUEST_BYTES,
        )
    )
    return max(value, 1)


def image_root() -> Path:
    """Resolve the complete administrator-configured listing-image directory.

    Relative paths resolve from the Flask-AAS project root, matching the host
    user-image storage contract. Absolute paths are used as configured.
    AutoGrid360 does not append an implicit storage suffix.
    """

    configured = listing_images_path().strip()
    if not configured:
        raise RuntimeError("Listing Images Path is not configured.")
    if "\x00" in configured:
        raise RuntimeError("Listing Images Path contains an invalid null byte.")

    root = Path(configured).expanduser()
    if not root.is_absolute():
        root = Path(current_app.root_path).parent / root
    return root.resolve()


def image_path(storage_key: str) -> Path:
    """Resolve an internally generated storage key beneath the image root."""

    root = image_root()
    path = (root / storage_key).resolve()
    if root != path and root not in path.parents:
        raise ValueError("AutoGrid360 image storage key escaped the configured root")
    return path


def _read_upload(upload) -> bytes:
    limit = max_image_bytes()
    payload = upload.stream.read(limit + 1)
    if not payload:
        raise ImageUploadError("The uploaded image is empty.")
    if len(payload) > limit:
        raise ImageUploadError(
            f"Each image must be {limit // (1024 * 1024)} MB or smaller."
        )
    return payload


def _load_image(payload: bytes) -> Image.Image:
    source = None
    normalized = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(io.BytesIO(payload))
            if source.format not in ALLOWED_IMAGE_FORMATS:
                raise ImageUploadError("Only JPEG, PNG, and WebP images are supported.")

            width, height = source.size
            if width <= 0 or height <= 0 or width * height > max_image_pixels():
                raise ImageUploadError("The uploaded image dimensions are too large.")

            # JPEG decoders can often downsample while decoding. This reduces
            # peak memory before the final high-quality LANCZOS resize below.
            source.draft("RGB", DISPLAY_MAX_SIZE)
            source.load()
            normalized = ImageOps.exif_transpose(source)
            if normalized is not source:
                source.close()
                source = None

            if normalized.mode == "RGB":
                result = normalized
                if normalized is source:
                    source = None
                normalized = None
                return result

            result = normalized.convert("RGB")
            normalized.close()
            normalized = None
            return result
    except ImageUploadError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageUploadError("The uploaded image dimensions are too large.") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ImageUploadError("The uploaded file is not a valid supported image.") from None
    finally:
        if normalized is not None:
            normalized.close()
        if source is not None:
            source.close()


def _atomic_save_jpeg(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        image.save(temporary, format="JPEG", quality=88, optimize=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def store_listing_image(listing_id: int, upload) -> dict:
    """Validate one upload and store normalized display and thumbnail JPEGs."""

    display = _load_image(_read_upload(upload))
    token = uuid4().hex
    relative_dir = Path(str(listing_id))
    storage_key = str(relative_dir / f"{token}.jpg")
    thumbnail_key = str(relative_dir / f"{token}_thumb.jpg")

    display.thumbnail(DISPLAY_MAX_SIZE, Image.Resampling.LANCZOS)
    thumbnail = display.copy()
    thumbnail.thumbnail(THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)

    display_path = image_path(storage_key)
    thumbnail_path = image_path(thumbnail_key)
    try:
        _atomic_save_jpeg(display, display_path)
        _atomic_save_jpeg(thumbnail, thumbnail_path)
        width, height = display.size
    except Exception:
        display_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        raise
    finally:
        thumbnail.close()
        display.close()

    return {
        "storage_key": storage_key,
        "thumbnail_key": thumbnail_key,
        "width": width,
        "height": height,
    }


def delete_image_files(image) -> None:
    """Best-effort removal of one image's stored display and thumbnail files."""

    for storage_key in (image.storage_key, image.thumbnail_key):
        try:
            image_path(storage_key).unlink(missing_ok=True)
        except OSError:
            logger.exception(
                "AutoGrid360 image cleanup failed for image_id=%s key=%s",
                image.id,
                storage_key,
            )
