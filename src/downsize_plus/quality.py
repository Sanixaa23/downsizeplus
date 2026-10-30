"""What the compression cost, measured against the original.

Every video compressor says the result "looks good". This one prints a number
the user can check: SSIM, the structural similarity between each output frame
and the matching source frame, averaged over the clip, computed by ffmpeg's
own `ssim` filter.

It is measured the way the viewer experiences it. When the plan scaled the
picture down, the output is scaled back up to the source size before the
comparison, so the resolution that was given up counts as loss rather than
being quietly compared at the smaller size where it would not show. When
the plan dropped the frame rate, the source is resampled to the same rate,
so frame N is compared with frame N and not with whatever happened to land
next to it.

The words printed beside the number are a reading aid and they are mine, not
a standard's. The number is the measurement.
"""
from __future__ import annotations

import re
import subprocess
from typing import NamedTuple, Optional

from .budget import Plan
from .probe import Media, ffmpeg

_SSIM = re.compile(r"SSIM .*?All:(?P<all>[0-9.]+) \((?P<db>[0-9.inf]+)\)")

BANDS = (
    (0.98, "hard to tell from the original"),
    (0.95, "good; fine detail softened"),
    (0.90, "visibly softer, still clear"),
    (0.0, "clearly degraded"),
)


class Quality(NamedTuple):
    ssim: float                # 0..1, 1 is identical
    db: Optional[float]        # the same figure as -10*log10(1-ssim)
    reading: str


def describe(ssim: float) -> str:
    for threshold, words in BANDS:
        if ssim >= threshold:
            return words
    return BANDS[-1][1]


def measure(source: Media, output_path: str, plan: Plan) -> Quality:
    """SSIM of `output_path` against the source, at the source's size."""
    reference = "[1:v]fps=%g,format=yuv420p[ref]" % plan.fps \
        if plan.fps < source.fps else "[1:v]format=yuv420p[ref]"
    graph = ("[0:v]scale=%d:%d:flags=bicubic,format=yuv420p[out];%s;"
             "[out][ref]ssim" % (source.width, source.height, reference))
    done = subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostdin",
         "-i", output_path, "-i", source.path,
         "-lavfi", graph, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    match = _SSIM.search(done.stderr or "")
    if not match:
        raise RuntimeError("ffmpeg's ssim filter printed no result; the "
                           "comparison could not be made")
    ssim = float(match.group("all"))
    raw_db = match.group("db")
    db = None if raw_db == "inf" else float(raw_db)
    return Quality(ssim=ssim, db=db, reading=describe(ssim))
