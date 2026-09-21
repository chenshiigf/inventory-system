"""Validate, resize, and save product images as a main/thumbnail WebP pair."""

from __future__ import annotations

import os
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final
from pathlib import PurePosixPath
from urllib.parse import unquote

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_PRODUCT_IMAGE_BYTES: Final = 10 * 1024 * 1024
MAX_PRODUCT_IMAGE_PIXELS: Final = 40_000_000
MAIN_IMAGE_MAX_SIDE: Final = 1600
THUMBNAIL_MAX_SIDE: Final = 320
MAIN_WEBP_QUALITY: Final = 84
THUMBNAIL_WEBP_QUALITY: Final = 78

ALLOWED_IMAGE_FORMATS: Final = {
    "JPEG": ("jpg", {".jpg", ".jpeg"}),
    "PNG": ("png", {".png"}),
    "WEBP": ("webp", {".webp"}),
}

DEFAULT_PRODUCT_IMAGE_DIRECTORY: Final = (
    Path(__file__).resolve().parents[2] / "data" / "uploads" / "products"
)


class ProductImageProcessingError(Exception):
    def __init__(self, detail: str, status_code: int = 415) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ProductImageResult:
    image_path: str
    thumbnail_path: str


def _format_for_filename(original_filename: str | None) -> str | None:
    if not original_filename:
        return None

    # The original name is inspected only for a familiar extension. It is never
    # used as a directory or server-side filename.
    safe_name = unquote(original_filename).replace("\\", "/")
    suffix = PurePosixPath(safe_name).suffix.lower()
    for image_format, (_extension, extensions) in ALLOWED_IMAGE_FORMATS.items():
        if suffix in extensions:
            return image_format
    if suffix:
        raise ProductImageProcessingError("仅支持 JPG、PNG、WEBP 格式的图片。")
    return None


def _encode_webp(image: Image.Image, max_side: int, quality: int) -> bytes:
    resized = image.copy()
    resized.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    output = BytesIO()
    resized.save(output, format="WEBP", quality=quality, method=5)
    return output.getvalue()


def _process_image_bytes(
    data: bytes,
    original_filename: str | None,
) -> tuple[bytes, bytes]:
    expected_format = _format_for_filename(original_filename)

    if not data:
        raise ProductImageProcessingError("图片文件不能为空。", status_code=400)
    if len(data) > MAX_PRODUCT_IMAGE_BYTES:
        raise ProductImageProcessingError("图片不能超过 10MB。", status_code=413)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                image_format = probe.format
                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise ProductImageProcessingError(
                        "仅支持 JPG、PNG、WEBP 格式的图片。"
                    )
                if expected_format is not None and expected_format != image_format:
                    raise ProductImageProcessingError(
                        "图片内容与文件扩展名不匹配，请选择有效的 JPG、PNG 或 WEBP 图片。",
                        status_code=400,
                    )
                width, height = probe.size
                if width * height > MAX_PRODUCT_IMAGE_PIXELS:
                    raise ProductImageProcessingError(
                        "图片分辨率过大，请缩小图片后重试。", status_code=413
                    )
                if getattr(probe, "is_animated", False) or getattr(
                    probe, "n_frames", 1
                ) > 1:
                    raise ProductImageProcessingError(
                        "暂不支持动画图片，请上传静态图片。", status_code=415
                    )
                probe.verify()

            # Reopen after verify, correct EXIF orientation, and decode the
            # pixels before producing either output file.
            with Image.open(BytesIO(data)) as source:
                oriented = ImageOps.exif_transpose(source)
                oriented.load()
                has_transparency = (
                    "A" in oriented.getbands()
                    or "transparency" in oriented.info
                )
                normalized = oriented.convert("RGBA" if has_transparency else "RGB")
                main_bytes = _encode_webp(
                    normalized, MAIN_IMAGE_MAX_SIDE, MAIN_WEBP_QUALITY
                )
                thumbnail_bytes = _encode_webp(
                    normalized, THUMBNAIL_MAX_SIDE, THUMBNAIL_WEBP_QUALITY
                )
    except ProductImageProcessingError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ProductImageProcessingError(
            "图片分辨率过大，请缩小图片后重试。", status_code=413
        ) from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
        raise ProductImageProcessingError(
            "无法读取图片内容，请选择有效的 JPG、PNG 或 WEBP 图片。",
            status_code=400,
        ) from error

    return main_bytes, thumbnail_bytes


def _write_temp_file(directory: Path, stem: str, contents: bytes) -> Path:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{stem}-",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(contents)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        return temporary_path
    except OSError:
        if temporary_path is not None:
            _remove_files([temporary_path])
        raise


def _remove_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Best-effort cleanup must not hide the original save error.
            pass


def _save_webp_pair(
    product_directory: Path,
    main_bytes: bytes,
    thumbnail_bytes: bytes,
) -> str:
    root = product_directory.resolve()
    main_directory = root / "main"
    thumbnail_directory = root / "thumbs"
    main_directory.mkdir(parents=True, exist_ok=True)
    thumbnail_directory.mkdir(parents=True, exist_ok=True)

    for _attempt in range(5):
        stem = uuid.uuid4().hex
        main_path = main_directory / f"{stem}.webp"
        thumbnail_path = thumbnail_directory / f"{stem}.webp"
        temporary_paths: list[Path] = []
        created_paths: list[Path] = []
        try:
            temporary_main = _write_temp_file(main_directory, stem, main_bytes)
            temporary_paths.append(temporary_main)
            temporary_thumbnail = _write_temp_file(
                thumbnail_directory, stem, thumbnail_bytes
            )
            temporary_paths.append(temporary_thumbnail)

            # Hard-linking a fully written temporary file creates each final
            # name atomically and refuses to overwrite an existing UUID.
            os.link(temporary_main, main_path)
            created_paths.append(main_path)
            os.link(temporary_thumbnail, thumbnail_path)
            created_paths.append(thumbnail_path)
        except FileExistsError:
            _remove_files(created_paths)
            continue
        except OSError:
            _remove_files(created_paths)
            raise
        finally:
            _remove_files(temporary_paths)

        return f"products/main/{stem}.webp"

    raise OSError("Could not allocate a unique product image filename")


def process_and_save_product_image(
    data: bytes,
    original_filename: str | None = None,
    content_type: str | None = None,
    *,
    storage_directory: Path | None = None,
) -> ProductImageResult:
    """Create and save a WebP main image and thumbnail from shared raw bytes.

    ``content_type`` is accepted so UploadFile and future Excel extractors can
    share this API, but Pillow's decoded image format is authoritative.
    ``original_filename`` is never used to construct a path.
    """
    del content_type
    main_bytes, thumbnail_bytes = _process_image_bytes(data, original_filename)
    product_directory = storage_directory or DEFAULT_PRODUCT_IMAGE_DIRECTORY
    main_path = _save_webp_pair(product_directory, main_bytes, thumbnail_bytes)
    stem = PurePosixPath(main_path).name
    return ProductImageResult(
        image_path=main_path,
        thumbnail_path=f"products/thumbs/{stem}",
    )
