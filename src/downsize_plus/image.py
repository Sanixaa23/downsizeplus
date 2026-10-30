"""Still images: the highest quality that fits, found by measuring.

An image encoder takes a quality number and gives back however many bytes it
gives back; there is no way to ask for a size. So the size is searched for,
and every candidate is a real encode held in memory: the number compared
with the limit is the file's actual length, not a prediction of it.

Pixels are given up before quality is. At quality 30 a JPEG is visibly made
of 8x8 blocks, while the same photo at 80% of the width and quality 70 just
looks like a smaller photo. So the picture is shrunk in 0.8 steps until
quality QUALITY_GOOD fits, and then the quality is binary-searched upward
from there. Only when even the smallest allowed picture (MIN_LONG_EDGE on
its long side) does not fit at QUALITY_GOOD is the quality let down toward
QUALITY_LOW -- and if that fails too, nothing is written.

Format, unless asked otherwise: a JPEG stays JPEG; anything with transparency
becomes WebP, because JPEG cannot carry it; everything else becomes WebP,
which holds both photographs and screenshots well at small sizes. PNG is
never an output -- it is lossless, has no quality to search, and is almost
never the way to make an image smaller.

Two side effects, both stated rather than discovered:

  * The rotation flag is applied to the pixels, so the result displays the
    right way up everywhere, including places that ignore the flag.
  * All other metadata is dropped. That includes GPS coordinates, which is
    usually what people want from a file they are about to post, and the
    camera model and capture time, which some people want to keep.
"""
from __future__ import annotations

import io
import os
from typing import List, Optional, Tuple

from .place import (AlreadyFits, CannotFit, Shrunk, commit, discard,
                    guard, workspace)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif",
                    ".tiff", ".heic", ".heif", ".avif")

QUALITY_LOW = 30
QUALITY_GOOD = 60
QUALITY_HIGH = 95
SCALE_STEP = 0.8
MIN_LONG_EDGE = 320

FORMATS = {"jpeg": ("JPEG", ".jpg"), "webp": ("WEBP", ".webp")}


class Unreadable(RuntimeError):
    pass


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise Unreadable("images need Pillow: pip install Pillow")
    return Image, ImageOps


def open_upright(path: str):
    Image, ImageOps = _pillow()
    if path.lower().endswith((".heic", ".heif")):
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError:
            raise Unreadable(
                "%s is HEIC, which needs the pillow-heif plugin: "
                "pip install pillow-heif" % os.path.basename(path))
    try:
        with Image.open(path) as image:
            if getattr(image, "n_frames", 1) > 1:
                # Saving it as a still would quietly keep frame one.
                raise Unreadable(
                    "%s is animated (%d frames); animated WebP and APNG are "
                    "not supported, convert it to GIF or MP4 first"
                    % (os.path.basename(path), image.n_frames))
            upright = ImageOps.exif_transpose(image)
            upright.load()
            return upright
    except Unreadable:
        raise
    except Image.DecompressionBombError:
        raise Unreadable("%s is larger than Pillow will decode safely"
                         % os.path.basename(path))
    except Exception as error:
        raise Unreadable("%s: %s" % (os.path.basename(path), error))


def _has_alpha(image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or \
        (image.mode == "P" and "transparency" in image.info)


def choose_format(path: str, image, requested: Optional[str] = None
                  ) -> Tuple[str, str]:
    if requested:
        return FORMATS[requested]
    if _has_alpha(image):
        return FORMATS["webp"]
    if path.lower().endswith((".jpg", ".jpeg")):
        return FORMATS["jpeg"]
    return FORMATS["webp"]


def encode(image, fmt: str, quality: int) -> bytes:
    Image, _ = _pillow()
    buffer = io.BytesIO()
    if fmt == "JPEG":
        if _has_alpha(image):
            # JPEG has no transparency. Flatten onto white, which is what
            # almost every viewer would have shown behind it anyway.
            rgba = image.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.split()[3])
            image = flat
        image.convert("RGB").save(
            buffer, "JPEG", quality=quality, optimize=True, progressive=True,
            subsampling=0 if quality >= 90 else 2)
    else:
        mode = "RGBA" if _has_alpha(image) else "RGB"
        image.convert(mode).save(buffer, "WEBP", quality=quality, method=4)
    return buffer.getvalue()


def _scaled(image, scale: float):
    if scale >= 1.0:
        return image
    Image, _ = _pillow()
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


def default_output(path: str, extension: str) -> str:
    stem, _ = os.path.splitext(path)
    return stem + ".downsized" + extension


def shrink(path: str, limit: int, out_path: Optional[str] = None,
           overwrite: bool = False, fmt: Optional[str] = None):
    """Shrunk, AlreadyFits or CannotFit."""
    size = os.path.getsize(path)
    if size <= limit:
        return AlreadyFits(size=size, limit=limit)

    if fmt is None and out_path:
        named = os.path.splitext(out_path)[1].lower()
        fmt = {".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}.get(named)
    original = open_upright(path)
    pil_format, extension = choose_format(path, original, fmt)
    out_path = out_path or default_output(path, extension)
    guard(path, out_path, overwrite)

    attempts: List[str] = []

    def attempt(picture, quality: int) -> bytes:
        data = encode(picture, pil_format, quality)
        attempts.append("%dx%d q%d -> %d bytes" % (
            picture.width, picture.height, quality, len(data)))
        return data

    def best_between(picture, lo: int, hi: int, floor_data: bytes):
        """Highest quality in lo..hi that fits; floor_data fits at lo-1."""
        best_quality, best = lo - 1, floor_data
        while lo <= hi:
            middle = (lo + hi) // 2
            data = attempt(picture, middle)
            if len(data) <= limit:
                best_quality, best = middle, data
                lo = middle + 1
            else:
                hi = middle - 1
        return best_quality, best

    smallest_scale = min(1.0, MIN_LONG_EDGE / float(max(original.size)))
    scale = 1.0
    while True:
        picture = _scaled(original, scale)
        good = attempt(picture, QUALITY_GOOD)
        if len(good) <= limit:
            best_quality, best = best_between(
                picture, QUALITY_GOOD + 1, QUALITY_HIGH, good)
            break
        if scale <= smallest_scale:
            # The smallest picture allowed, and still too big at a decent
            # quality: let the quality down, but no further than LOW.
            lowest = attempt(picture, QUALITY_LOW)
            if len(lowest) > limit:
                return CannotFit(
                    limit=limit, smallest_honest_bytes=len(lowest),
                    reason="even %dpx on the long edge at quality %d is %d "
                           "bytes" % (max(picture.size), QUALITY_LOW,
                                      len(lowest)))
            best_quality, best = best_between(
                picture, QUALITY_LOW + 1, QUALITY_GOOD - 1, lowest)
            break
        scale = max(scale * SCALE_STEP, smallest_scale)

    work = workspace()
    try:
        candidate = os.path.join(work, "candidate" + extension)
        with open(candidate, "wb") as handle:
            handle.write(best)
        written = commit(candidate, out_path, limit)
    finally:
        discard(work)

    resized = "" if scale >= 1.0 else ", %dx%d -> %dx%d" % (
        original.width, original.height, picture.width, picture.height)
    return Shrunk(
        path=out_path, size=written, limit=limit,
        summary="%s quality %d%s" % (pil_format, best_quality, resized),
        attempts=tuple(attempts))
