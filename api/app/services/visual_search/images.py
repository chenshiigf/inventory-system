"""The identical preprocessing path is used for gallery and query images."""
import hashlib
import math
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
SEARCH_MAX_BYTES = 2 * 1024 * 1024
SEARCH_MAX_PIXELS = 3_000_000


class VisualError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def prepare_image(
    data: bytes,
    crop: tuple[float, float, float, float] | None = None,
    *,
    max_bytes: int = MAX_BYTES,
    max_pixels: int = MAX_PIXELS,
) -> bytes:
    if not data or len(data) > max_bytes:
        limit_mib = max_bytes / (1024 * 1024)
        limit_text = "10MB" if max_bytes == MAX_BYTES else f"{limit_mib:g}MiB"
        raise VisualError(f"图片不能为空且不能超过 {limit_text}", 413 if data else 400)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise VisualError("仅支持 JPG、PNG、WEBP 静态图片", 415)
                width, height = source.size
                if width * height > max_pixels:
                    pixel_limit = f"{max_pixels // 100_000}万" if max_pixels % 100_000 == 0 else f"{max_pixels:,}"
                    raise VisualError(f"图片分辨率不能超过 {pixel_limit} 像素", 413)
                if getattr(source, "n_frames", 1) > 1:
                    raise VisualError("不支持动画图片，请上传静态图片", 413)

                # Pillow reads headers before pixel data. Validate dimensions and
                # format above, then decode the original only once. Crop before
                # converting modes so a small selection does not expand the
                # entire source image into an RGBA buffer.
                image = ImageOps.exif_transpose(source)
                if crop is not None:
                    x, y, w, h = crop
                    if not all(math.isfinite(v) for v in crop) or min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
                        raise VisualError("框选区域无效，请在原图内重新框选")
                    left, top = round(x * image.width), round(y * image.height)
                    right, bottom = round((x + w) * image.width), round((y + h) * image.height)
                    if right - left < 10 or bottom - top < 10:
                        raise VisualError("框选区域太小，请至少选取 10×10 像素")
                    image = image.crop((left, top, right, bottom))
                has_alpha = "A" in image.getbands() or "transparency" in image.info
                if has_alpha:
                    if image.mode != "RGBA":
                        image = image.convert("RGBA")
                    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background = image if image.mode == "RGB" else image.convert("RGB")
                    background.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                out = BytesIO()
                background.save(out, "JPEG", quality=90)
                return out.getvalue()
    except VisualError:
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise VisualError("无法读取有效图片，请重新选择 JPG、PNG 或 WEBP") from error


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gallery_image(root: Path, identifier: str) -> bytes:
    # Never retrieve remote URLs, and resolve symlinks before checking containment.
    if not identifier or ":" in identifier or "\\" in identifier:
        raise VisualError("商品图片路径无效")
    target = (root / identifier).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise VisualError("商品主图文件不存在或路径无效")
    with target.open("rb") as file:
        # Large read(n) allocates a 10MB buffer even for a tiny product photo on
        # Windows. Keep the same upper bound without allocating it per image.
        chunks, remaining = [], MAX_BYTES + 1
        while remaining:
            chunk = file.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
