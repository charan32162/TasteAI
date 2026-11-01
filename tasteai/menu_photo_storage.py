"""Private, local-first storage for user-supplied menu images."""

import hashlib
import os
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from secrets import token_hex


class MenuPhotoStorageError(ValueError):
    pass


class MenuPhotoDecoderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredMenuPhoto:
    storage_key: str
    sha256: str
    content_type: str
    byte_size: int
    width: int
    height: int


def storage_directory() -> Path:
    configured = os.getenv("TASTEAI_MENU_PHOTO_STORAGE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".tasteai" / "private-menu-photos"


def maximum_upload_bytes() -> int:
    return _bounded_env_int("TASTEAI_MENU_PHOTO_MAX_BYTES", 10 * 1024 * 1024, 1024, 50 * 1024 * 1024)


def maximum_image_pixels() -> int:
    return _bounded_env_int("TASTEAI_MENU_PHOTO_MAX_PIXELS", 20_000_000, 1, 100_000_000)


def validate_image(data: bytes, declared_content_type: str | None = None) -> tuple[str, int, int, str]:
    if not isinstance(data, bytes) or not data:
        raise MenuPhotoStorageError("A non-empty image is required.")
    if len(data) > maximum_upload_bytes():
        raise MenuPhotoStorageError("The menu photo exceeds the configured upload limit.")
    if not (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    ):
        raise MenuPhotoStorageError("The uploaded bytes are not a supported image.")
    try:
        from PIL import Image
    except ImportError as error:
        raise MenuPhotoDecoderUnavailable("Menu photo decoding requires the declared Pillow dependency.") from error

    media_types = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
                content_type = media_types.get(image_format)
                if content_type is None:
                    raise MenuPhotoStorageError("Only JPEG, PNG, and WebP menu photos are supported.")
                if width < 1 or height < 1 or width * height > maximum_image_pixels():
                    raise MenuPhotoStorageError("The decoded menu photo dimensions exceed the configured limit.")
                image.verify()
            with Image.open(BytesIO(data)) as decoded:
                if decoded.format != image_format or decoded.size != (width, height):
                    raise MenuPhotoStorageError("The menu photo could not be validated consistently.")
                decoded.load()
    except MenuPhotoStorageError:
        raise
    except Exception as error:
        raise MenuPhotoStorageError("The uploaded bytes are not a valid supported image.") from error

    if declared_content_type:
        declared = declared_content_type.split(";", 1)[0].strip().lower()
        if declared != content_type:
            raise MenuPhotoStorageError("The declared content type does not match the image bytes.")
    return content_type, width, height, hashlib.sha256(data).hexdigest()


def store_menu_photo(data: bytes, declared_content_type: str | None = None) -> StoredMenuPhoto:
    content_type, width, height, digest = validate_image(data, declared_content_type)
    root = storage_directory().expanduser().resolve()
    public_web_root = Path(__file__).with_name("web").resolve()
    if root == public_web_root or public_web_root in root.parents:
        raise MenuPhotoStorageError("Menu photo storage must be outside the public web directory.")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
    key = token_hex(32)
    destination = root / f"{key}{suffix}"
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stored_file:
            stored_file.write(data)
    except FileExistsError as error:
        raise MenuPhotoStorageError("A private storage key collision occurred; retry the upload.") from error
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise MenuPhotoStorageError("The private menu photo could not be stored.") from error
    return StoredMenuPhoto(key, digest, content_type, len(data), width, height)


def read_menu_photo(storage_key: str) -> bytes:
    """Internal-only accessor; no HTTP route serves this data."""
    if not isinstance(storage_key, str) or len(storage_key) != 64 or any(c not in "0123456789abcdef" for c in storage_key):
        raise MenuPhotoStorageError("Menu photo storage key is invalid.")
    root = storage_directory().expanduser().resolve()
    matches = tuple(root.glob(f"{storage_key}.*"))
    if len(matches) != 1 or matches[0].parent.resolve() != root or matches[0].is_symlink():
        raise MenuPhotoStorageError("Stored menu photo is unavailable.")
    return matches[0].read_bytes()


def remove_menu_photo(storage_key: str) -> None:
    """Remove only an internally generated object key during failed transactions."""
    if not isinstance(storage_key, str) or len(storage_key) != 64 or any(c not in "0123456789abcdef" for c in storage_key):
        raise MenuPhotoStorageError("Menu photo storage key is invalid.")
    root = storage_directory().expanduser().resolve()
    matches = tuple(root.glob(f"{storage_key}.*"))
    for path in matches:
        if path.parent.resolve() == root and not path.is_symlink():
            path.unlink(missing_ok=True)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise MenuPhotoStorageError(f"{name} must be an integer.") from error
    if not minimum <= value <= maximum:
        raise MenuPhotoStorageError(f"{name} must be between {minimum} and {maximum}.")
    return value
