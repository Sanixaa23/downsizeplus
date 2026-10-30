"""Animated GIFs: stay a GIF, and fit.

A GIF has three dials -- picture size, frame rate, palette size -- and no
bitrate, so there is nothing to compute in advance the way there is for
video. Instead the combinations are laid out in order of how large they are
expected to come out, best-looking first, and searched by halving: encode
the middle one, measure it, and move toward smaller or larger. About eight
encodes cover a ladder of over a hundred settings.

The expectation used for ordering is rough, and it only decides which
encodes are tried. It never decides the answer: the result is the best
setting that was actually encoded and measured under the limit.

It stays a GIF on purpose. An MP4 of the same animation is often a tenth of
the size, but a GIF is what was asked to be made smaller, and silently
handing back a different kind of file is not that. Pass `-o NAME.mp4` to get
the MP4 instead; it then goes through the video path like any clip.
"""
from __future__ import annotations

import os
import subprocess
from typing import List, NamedTuple, Optional

from .place import (AlreadyFits, CannotFit, Shrunk, commit, discard,
                    guard, workspace)
from .probe import Media, ffmpeg

SCALES = (1.0, 0.85, 0.7, 0.6, 0.5, 0.4, 0.33, 0.25)
FPS_STEPS = (20.0, 15.0, 12.0, 10.0)
COLOURS = (256, 128, 64, 32)
# How much each palette size tends to save, for ordering only.
COLOUR_FACTOR = {256: 1.0, 128: 0.85, 64: 0.7, 32: 0.58}
MIN_EDGE = 120


class Setting(NamedTuple):
    scale: float
    fps: float
    colours: int
    expected: float           # relative size, for ordering only


class EncodeFailed(RuntimeError):
    pass


def ladder(media: Media) -> List[Setting]:
    rates = [media.fps] + [f for f in FPS_STEPS if f < media.fps]
    long_edge = max(media.width, media.height)
    out = []
    for scale in SCALES:
        if long_edge * scale < MIN_EDGE and scale != SCALES[0]:
            continue
        for fps in rates:
            for colours in COLOURS:
                expected = scale * scale * (fps / media.fps) * \
                    COLOUR_FACTOR[colours]
                out.append(Setting(scale, fps, colours, expected))
    out.sort(key=lambda s: -s.expected)
    return out


def _encode(media: Media, setting: Setting, target: str) -> int:
    chain = []
    if setting.fps < media.fps:
        chain.append("fps=%g" % setting.fps)
    if setting.scale < 1.0:
        chain.append("scale=iw*%g:-1:flags=lanczos" % setting.scale)
    chain.append("split[a][b];[a]palettegen=max_colors=%d:stats_mode=diff[p];"
                 "[b][p]paletteuse=dither=bayer:bayer_scale=3:"
                 "diff_mode=rectangle" % setting.colours)
    done = subprocess.run(
        [ffmpeg(), "-y", "-hide_banner", "-nostdin", "-i", media.path,
         "-vf", ",".join(chain), "-loop", "0", target],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0 or not os.path.exists(target):
        tail = "\n".join((done.stderr or "").strip().splitlines()[-4:])
        raise EncodeFailed("ffmpeg failed on the GIF:\n%s" % tail)
    return os.path.getsize(target)


def default_output(path: str) -> str:
    stem, _ = os.path.splitext(path)
    return stem + ".downsized.gif"


def shrink(media: Media, limit: int, out_path: Optional[str] = None,
           overwrite: bool = False):
    """Shrunk, AlreadyFits or CannotFit."""
    if media.size <= limit:
        return AlreadyFits(size=media.size, limit=limit)

    out_path = out_path or default_output(media.path)
    guard(media.path, out_path, overwrite)

    settings = ladder(media)
    work = workspace()
    attempts: List[str] = []
    try:
        measured = {}

        def size_of(index: int) -> int:
            if index not in measured:
                path = os.path.join(work, "try%03d.gif" % index)
                measured[index] = _encode(media, settings[index], path)
                s = settings[index]
                attempts.append("%d%% size, %g fps, %d colours -> %d bytes" % (
                    round(s.scale * 100), s.fps, s.colours, measured[index]))
            return measured[index]

        last = len(settings) - 1
        if size_of(last) > limit:
            return CannotFit(
                limit=limit, smallest_honest_bytes=measured[last],
                reason="the smallest setting tried (%d%% size, %g fps, %d "
                       "colours) is still %d bytes"
                       % (round(settings[last].scale * 100),
                          settings[last].fps, settings[last].colours,
                          measured[last]))

        # Find the best-looking setting that fits, by halving. Anything
        # measured under the limit along the way is a candidate.
        lo, hi = 0, last
        while lo < hi:
            middle = (lo + hi) // 2
            if size_of(middle) <= limit:
                hi = middle
            else:
                lo = middle + 1

        fitting = [i for i, size in measured.items() if size <= limit]
        best = min(fitting)
        chosen = settings[best]
        candidate = os.path.join(work, "try%03d.gif" % best)
        written = commit(candidate, out_path, limit)
    finally:
        discard(work)

    return Shrunk(
        path=out_path, size=written, limit=limit,
        summary="GIF at %d%% size, %g fps, %d colours" % (
            round(chosen.scale * 100), chosen.fps, chosen.colours),
        attempts=tuple(attempts))
