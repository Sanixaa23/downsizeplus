"""What is in the file, read from ffmpeg's own description of it.

Everything downstream is arithmetic on four numbers -- duration, width,
height, frame rate -- plus whether there is an audio track. Get the duration
wrong and the bitrate is wrong by the same factor, and the output misses the
size limit by that factor, so this module refuses rather than guesses when
any of them cannot be read.

`ffprobe` would be the conventional tool, but the bundled ffmpeg from
`imageio-ffmpeg` ships without it, and requiring a second binary for four
numbers is a poor trade. `ffmpeg -i` prints the same facts to stderr and
exits non-zero because no output was named; the text is the result either
way, so it is parsed and the exit code is ignored.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import NamedTuple, Optional


class FfmpegMissing(RuntimeError):
    pass


class Unreadable(RuntimeError):
    """The file exists but ffmpeg could not describe it as a video."""


_DURATION = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_VIDEO = re.compile(
    r"Stream #\d+:\d+[^:]*: Video: (?P<codec>\w+).*?, "
    r"(?P<w>\d{2,5})x(?P<h>\d{2,5})")
_FPS = re.compile(r"(\d+(?:\.\d+)?) fps")
_TBR = re.compile(r"(\d+(?:\.\d+)?)k? tbr")
_AUDIO = re.compile(r"Stream #\d+:\d+[^:]*: Audio: (?P<codec>\w+)")
_ROTATE = re.compile(r"rotate\s*:\s*(-?\d+)|displaymatrix: rotation of (-?\d+(?:\.\d+)?)")


class Media(NamedTuple):
    path: str
    size: int                  # bytes on disk
    duration: float            # seconds
    width: int                 # as displayed, rotation applied
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    audio_codec: Optional[str]

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def bitrate(self) -> float:
        """Average bits per second of the whole file as it stands."""
        return self.size * 8.0 / self.duration if self.duration > 0 else 0.0


def ffmpeg() -> str:
    """The ffmpeg binary: the bundled one if present, else one on PATH."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FfmpegMissing(
        "ffmpeg not found. Install it, or `pip install imageio-ffmpeg` to "
        "use the bundled build.")


def describe(path: str) -> str:
    """ffmpeg's stderr description of `path`."""
    done = subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostdin", "-i", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stderr or ""


def parse(path: str, report: str, size: int) -> Media:
    """Turn ffmpeg's description into a Media, or raise Unreadable."""
    name = os.path.basename(path)

    duration_match = _DURATION.search(report)
    if not duration_match:
        raise Unreadable("%s: ffmpeg reported no duration. It may be "
                         "truncated, still being written, or not a video."
                         % name)
    duration = (int(duration_match.group(1)) * 3600
                + int(duration_match.group(2)) * 60
                + float(duration_match.group(3)))
    if duration <= 0:
        raise Unreadable("%s: duration is zero, so there is no bitrate to "
                         "compute." % name)

    video = _VIDEO.search(report)
    if not video:
        raise Unreadable("%s has no video stream." % name)
    width, height = int(video.group("w")), int(video.group("h"))

    # Phone footage is often stored landscape with a rotation flag. The
    # output is encoded upright, so the size budget has to use the size the
    # viewer actually sees.
    rotation = _ROTATE.search(report)
    if rotation:
        degrees = abs(int(float(rotation.group(1) or rotation.group(2))))
        if degrees % 180 == 90:
            width, height = height, width

    line_end = report.find("\n", video.start())
    stream_line = report[video.start():line_end if line_end != -1 else None]
    fps_match = _FPS.search(stream_line) or _TBR.search(stream_line)
    if not fps_match:
        raise Unreadable("%s: no frame rate in the video stream description."
                         % name)
    fps = float(fps_match.group(1))
    if fps <= 0 or fps > 1000:
        raise Unreadable("%s: implausible frame rate %g." % (name, fps))

    audio = _AUDIO.search(report)
    return Media(
        path=path,
        size=size,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        video_codec=video.group("codec"),
        has_audio=audio is not None,
        audio_codec=audio.group("codec") if audio else None,
    )


def probe(path: str) -> Media:
    if not os.path.exists(path):
        raise FileNotFoundError("no such file: %s" % path)
    return parse(path, describe(path), os.path.getsize(path))
