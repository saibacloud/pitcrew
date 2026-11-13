# Image prep for AI calls and storage

import io

from PIL import Image, ImageDraw, ImageOps


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=92)
    return buf.getvalue()


def pin_images(raw_bytes: bytes, x_pct: float | None, y_pct: float | None) -> list[bytes]:
    """JPEGs for pin research: the full photo with the pin marked, plus a close-up.

    Saving fresh JPEGs drops EXIF. Without a pin position, only the photo is returned.
    """
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes))).convert('RGB')
    if x_pct is None or y_pct is None:
        return [_jpeg(img)]

    w, h = img.size
    cx, cy = w * x_pct / 100, h * y_pct / 100

    # Crop before marking so the ring never hides part numbers in the close-up
    side = max(256, int(min(w, h) * 0.35))
    left = int(min(max(cx - side / 2, 0), max(w - side, 0)))
    top = int(min(max(cy - side / 2, 0), max(h - side, 0)))
    close_up = img.crop((left, top, min(left + side, w), min(top + side, h)))

    marked = img.copy()
    draw = ImageDraw.Draw(marked)
    r = max(12, int(min(w, h) * 0.04))
    line = max(3, r // 5)
    # White halo keeps the red ring visible on red or dark components
    draw.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), outline='white', width=line + 4)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline='red', width=line)
    draw.line((cx - r * 1.6, cy, cx - r * 0.5, cy), fill='red', width=line)
    draw.line((cx + r * 0.5, cy, cx + r * 1.6, cy), fill='red', width=line)
    draw.line((cx, cy - r * 1.6, cx, cy - r * 0.5), fill='red', width=line)
    draw.line((cx, cy + r * 0.5, cx, cy + r * 1.6), fill='red', width=line)

    return [_jpeg(marked), _jpeg(close_up)]


def compress_for_storage(raw_bytes: bytes, max_edge: int = 2048) -> bytes:
    """Resize image to max_edge on longest side, return compressed JPEG/PNG."""
    img = Image.open(io.BytesIO(raw_bytes))
    fmt = (img.format or 'JPEG').upper()

    # Apply EXIF orientation before stripping so the pixels are correctly rotated
    img = ImageOps.exif_transpose(img)

    # Strip EXIF
    save_kwargs: dict = {'format': fmt}
    if fmt in ('JPEG', 'WEBP'):
        save_kwargs['exif'] = b''
        save_kwargs['quality'] = 85

    # Resize if larger than max_edge
    w, h = img.size
    if max(w, h) > max_edge:
        ratio = max_edge / max(w, h)
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, **save_kwargs)
    return buf.getvalue()


def make_thumbnail(raw_bytes: bytes, size: int = 400) -> bytes:
    """Generate a square-ish thumbnail, always JPEG."""
    img = Image.open(io.BytesIO(raw_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')
    w, h = img.size
    ratio = size / max(w, h)
    new_size = (int(w * ratio), int(h * ratio))
    img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=80, exif=b'')
    return buf.getvalue()
