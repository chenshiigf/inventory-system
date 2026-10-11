"""The identical preprocessing path is used for gallery and query images."""
import hashlib
import math
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000


class VisualError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def prepare_image(data: bytes, crop: tuple[float, float, float, float] | None = None) -> bytes:
    if not data or len(data) > MAX_BYTES:
        raise VisualError("图片不能为空且不能超过 10MB", 413 if data else 400)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                if probe.format not in {"JPEG", "PNG", "WEBP"}:
                    raise VisualError("仅支持 JPG、PNG、WEBP 静态图片", 415)
                if probe.width * probe.height > MAX_PIXELS or getattr(probe, "n_frames", 1) > 1:
                    raise VisualError("图片过大或为动画，请上传静态图片", 413)
                probe.verify()
            with Image.open(BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGBA")
                if crop is not None:
                    x, y, w, h = crop
                    if not all(math.isfinite(v) for v in crop) or min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
                        raise VisualError("框选区域无效，请在原图内重新框选")
                    left, top = round(x * image.width), round(y * image.height)
                    right, bottom = round((x + w) * image.width), round((y + h) * image.height)
                    if right - left < 10 or bottom - top < 10:
                        raise VisualError("框选区域太小，请至少选取 10×10 像素")
                    image = image.crop((left, top, right, bottom))
                image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
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
