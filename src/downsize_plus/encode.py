"""The encode, and the only part of this program that keeps the promise.

`budget.py` aims. This module checks where the shot landed. Two-pass x264
lands within a percent or two of the requested bitrate on most footage and
misses by more on some -- static screens, sudden cuts, very short clips
where the container is a large share of the file. So the output is measured
after every encode, and if it is over the limit the video bitrate is scaled
down by the overshoot and the clip is encoded again.

If it is still over after the last attempt, the candidate is deleted and an
exception says by how much it missed. The output path is only ever written
by a rename of a file that has already been measured under the limit, so
there is no moment at which an oversize file sits where the caller asked
for the result.

Two other refusals, both about the user's files rather than the arithmetic:
the input is never the output, and an existing output is never overwritten
unless the caller says so.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Callable, List, NamedTuple, Optional

from .budget import Plan, overhead
from .probe import Media, ffmpeg

DEFAULT_ATTEMPTS = 4
# After an overshoot, aim this much below the corrected target as well, so a
# second miss of the same size does not happen.
RETRY_MARGIN = 0.97
PRESET = "medium"


class DidNotFit(RuntimeError):
    def __init__(self, best_size: int, limit: int, attempts: int):
        self.best_size = best_size
        self.limit = limit
        self.attempts = attempts
        super().__init__(
            "after %d attempts the smallest result was %d bytes, %d over the "
            "%d-byte limit; nothing was written"
            % (attempts, best_size, best_size - limit, limit))


class EncodeFailed(RuntimeError):
    pass


class Attempt(NamedTuple):
    video_kbps: int
    size: int
    fits: bool


class Result(NamedTuple):
    path: str
    size: int
    plan: Plan
    attempts: tuple            # of Attempt, in order

    @property
    def headroom(self) -> int:
        return self.plan.limit_bytes - self.size


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == \
            os.path.normcase(os.path.abspath(b))


def _filters(media: Media, plan: Plan) -> List[str]:
    chain: List[str] = []
    if plan.fps < media.fps:
        chain.append("fps=%g" % plan.fps)
    if (plan.width, plan.height) != (media.width, media.height):
        chain.append("scale=%d:%d:flags=lanczos" % (plan.width, plan.height))
    # Even when nothing is scaled, force 4:2:0. Many players -- Discord's
    # inline one among them -- will not play 4:4:4 or 10-bit H.264.
    chain.append("format=yuv420p")
    return ["-vf", ",".join(chain)]


def _run(command: List[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        tail = "\n".join((done.stderr or "").strip().splitlines()[-6:])
        raise EncodeFailed("ffmpeg failed:\n%s" % tail)


def _two_pass(media: Media, plan: Plan, video_kbps: int, work: str,
              candidate: str) -> None:
    log = os.path.join(work, "passlog")
    common = [ffmpeg(), "-y", "-hide_banner", "-nostdin", "-i", media.path,
              *_filters(media, plan),
              "-c:v", "libx264", "-preset", PRESET,
              "-b:v", "%dk" % video_kbps, "-passlogfile", log]

    _run(common + ["-pass", "1", "-an", "-f", "mp4", os.devnull])

    audio = (["-c:a", "aac", "-b:a", "%dk" % plan.audio_kbps, "-ac", "2"]
             if plan.audio_kbps > 0 else ["-an"])
    # +faststart moves the index to the front so the clip starts playing
    # before it has finished downloading.
    _run(common + ["-pass", "2", *audio, "-movflags", "+faststart",
                   candidate])


def encode(media: Media, plan: Plan, out_path: str,
           attempts: int = DEFAULT_ATTEMPTS, overwrite: bool = False,
           progress: Optional[Callable[[Attempt], None]] = None) -> Result:
    """Encode `media` to `out_path` under `plan.limit_bytes`, or raise."""
    if _same_file(media.path, out_path):
        raise ValueError("the output would replace the input; choose "
                         "another name")
    if os.path.exists(out_path) and not overwrite:
        raise FileExistsError("%s already exists; pass overwrite to replace "
                              "it" % out_path)

    work = tempfile.mkdtemp(prefix="downsize-")
    candidate = os.path.join(work, "candidate.mp4")
    history: List[Attempt] = []
    video_kbps = plan.video_kbps
    container = overhead(plan.duration, plan.fps, plan.audio_kbps > 0)

    try:
        for _ in range(max(1, attempts)):
            _two_pass(media, plan, video_kbps, work, candidate)
            size = os.path.getsize(candidate)
            fits = size <= plan.limit_bytes
            attempt = Attempt(video_kbps=video_kbps, size=size, fits=fits)
            history.append(attempt)
            if progress:
                progress(attempt)

            if fits:
                directory = os.path.dirname(os.path.abspath(out_path))
                os.makedirs(directory, exist_ok=True)
                # Measured first, placed second. The rename is the only
                # write to out_path that ever happens.
                shutil.move(candidate, out_path)
                return Result(path=out_path, size=size, plan=plan,
                              attempts=tuple(history))

            # Scale the video share by how far the streams overshot. Audio is
            # fixed-rate and the container cost barely moves, so the whole
            # excess has to come out of the video.
            audio_bytes = plan.audio_kbps * 1000.0 / 8.0 * plan.duration
            want = plan.limit_bytes - container - audio_bytes
            got = size - container - audio_bytes
            if got <= 0 or want <= 0:
                break
            video_kbps = int(video_kbps * (want / got) * RETRY_MARGIN)
            if video_kbps < 20:
                break

        smallest = min(a.size for a in history)
        raise DidNotFit(smallest, plan.limit_bytes, len(history))
    finally:
        shutil.rmtree(work, ignore_errors=True)
