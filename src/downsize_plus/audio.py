"""Audio: a bitrate from the budget, then measured, then retried lower.

Audio is the easy case for arithmetic. Size is bitrate times duration, so
the bitrate comes straight from the limit, and a constant-bitrate MP3 lands
within a fraction of a percent of it. AAC and Opus wander a little more, so
every encode is measured, and one that comes out over is redone at a lower
rate scaled by how far it missed. The output is only placed once measured
under the limit, the same as everything else here.

Codec follows the output's extension. With no output named: MP3 stays MP3,
M4A/AAC become M4A, Ogg and Opus become Opus, and everything else -- WAV,
FLAC, AIFF, WMA -- becomes MP3, the one format every phone, browser and
chat app plays.

Below 64 kbps the output is mono. Two channels at 32 kbps sound worse than
one channel at 32 kbps, and for the voice notes and recordings that end up
this small, the second channel is rarely carrying anything. Below a codec's
floor -- the rate under which speech stops being intelligible -- nothing is
written, and the message says what limit the recording could reach.

Embedded cover art is dropped: it is often a megabyte of JPEG riding along
in a file someone is trying to make small.
"""
from __future__ import annotations

import os
import subprocess
from typing import List, NamedTuple, Optional

from .place import (AlreadyFits, CannotFit, Shrunk, commit, discard, guard,
                    workspace)
from .probe import _AUDIO, _DURATION, Unreadable, describe, ffmpeg

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga",
                    ".opus", ".wma", ".aiff", ".aif", ".amr")

# Output extension -> (ffmpeg encoder, floor kbps, extra arguments).
CODECS = {
    ".mp3": ("libmp3lame", 16, []),
    ".m4a": ("aac", 16, ["-movflags", "+faststart"]),
    ".ogg": ("libopus", 8, []),
    ".opus": ("libopus", 8, []),
}
_DEFAULT_EXTENSION = {".mp3": ".mp3", ".m4a": ".m4a", ".aac": ".m4a",
                      ".ogg": ".ogg", ".oga": ".ogg", ".opus": ".opus"}

MONO_BELOW_KBPS = 64
# Rates MP3 can actually carry. LAME silently rounds anything else to a
# legal value, which can be upward, so the plan picks a legal one itself.
MP3_RATES_44K = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256,
                 320)
MP3_RATES_LOW = (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
MAX_KBPS = 320
# MPEG-1 (44.1 kHz) from here up; below it, MPEG-2 at 22.05 or 16 kHz.
MP3_44K_FROM_KBPS = 64
# Containers carry headers and per-packet tables on top of the audio.
OVERHEAD_FIXED = 8 * 1024
OVERHEAD_SHARE = 0.02
DEFAULT_ATTEMPTS = 4


class Sound(NamedTuple):
    path: str
    size: int
    duration: float
    codec: str
    channels: int


class EncodeFailed(RuntimeError):
    pass


def probe_audio(path: str) -> Sound:
    if not os.path.exists(path):
        raise FileNotFoundError("no such file: %s" % path)
    report = describe(path)
    name = os.path.basename(path)
    found = _DURATION.search(report)
    if not found:
        raise Unreadable("%s: ffmpeg reported no duration." % name)
    duration = (int(found.group(1)) * 3600 + int(found.group(2)) * 60
                + float(found.group(3)))
    if duration <= 0:
        raise Unreadable("%s: duration is zero." % name)
    stream = _AUDIO.search(report)
    if not stream:
        raise Unreadable("%s has no audio stream." % name)
    line_end = report.find("\n", stream.start())
    line = report[stream.start():line_end if line_end != -1 else None]
    channels = 1 if ", mono" in line else 2
    return Sound(path=path, size=os.path.getsize(path), duration=duration,
                 codec=stream.group("codec"), channels=channels)


def output_extension(path: str, out_path: Optional[str]) -> str:
    if out_path:
        extension = os.path.splitext(out_path)[1].lower()
        if extension not in CODECS:
            raise ValueError("cannot write audio as %s; use one of %s"
                             % (extension or "a file with no extension",
                                ", ".join(sorted(CODECS))))
        return extension
    return _DEFAULT_EXTENSION.get(os.path.splitext(path)[1].lower(), ".mp3")


def budget_kbps(limit: int, duration: float) -> float:
    usable = limit * (1.0 - OVERHEAD_SHARE) - OVERHEAD_FIXED
    return usable * 8.0 / duration / 1000.0


def legal_rate(extension: str, kbps: float):
    """(kbps, sample rate or None) the encoder will really use, never above
    `kbps`, or None if nothing legal is that low."""
    if extension != ".mp3":
        return (min(int(kbps), MAX_KBPS), None)
    if kbps >= MP3_44K_FROM_KBPS:
        fitting = [r for r in MP3_RATES_44K if r <= kbps]
        return (fitting[-1], 44100)
    fitting = [r for r in MP3_RATES_LOW if r <= kbps]
    if not fitting:
        return None
    rate = fitting[-1]
    return (rate, 22050 if rate >= 32 else 16000)


def default_output(path: str, extension: str) -> str:
    stem, _ = os.path.splitext(path)
    return stem + ".downsized" + extension


def _encode(sound: Sound, extension: str, kbps: int,
            sample_rate: Optional[int], channels: int, target: str) -> int:
    encoder, _, extra = CODECS[extension]
    command = [ffmpeg(), "-y", "-hide_banner", "-nostdin", "-i", sound.path,
               "-vn", "-map_metadata", "0", "-c:a", encoder,
               "-b:a", "%dk" % kbps, "-ac", str(channels)]
    if sample_rate:
        command += ["-ar", str(sample_rate)]
    command += extra + [target]
    done = subprocess.run(command, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0 or not os.path.exists(target):
        tail = "\n".join((done.stderr or "").strip().splitlines()[-4:])
        raise EncodeFailed("ffmpeg failed on the audio:\n%s" % tail)
    return os.path.getsize(target)


def shrink(sound: Sound, limit: int, out_path: Optional[str] = None,
           overwrite: bool = False, attempts: int = DEFAULT_ATTEMPTS):
    """Shrunk, AlreadyFits or CannotFit."""
    if sound.size <= limit:
        return AlreadyFits(size=sound.size, limit=limit)

    extension = output_extension(sound.path, out_path)
    out_path = out_path or default_output(sound.path, extension)
    guard(sound.path, out_path, overwrite)
    floor = CODECS[extension][1]

    def cannot(kbps: float) -> CannotFit:
        reachable = int(sound.duration * floor * 1000 / 8.0 /
                        (1.0 - OVERHEAD_SHARE) + OVERHEAD_FIXED)
        return CannotFit(
            limit=limit, smallest_honest_bytes=reachable,
            reason="%s of audio in this limit is %.1f kbps, under the %d kbps "
                   "floor for %s%s" % (
                       _clock(sound.duration), max(kbps, 0.0), floor,
                       extension,
                       "" if floor <= CODECS[".opus"][1] or kbps < CODECS[
                           ".opus"][1] else
                       " (Opus goes down to %d kbps: -o NAME.opus)"
                       % CODECS[".opus"][1]))

    kbps = min(budget_kbps(limit, sound.duration), MAX_KBPS)
    tried: List[str] = []
    work = workspace()
    try:
        for number in range(1, attempts + 1):
            legal = legal_rate(extension, kbps) if kbps >= floor else None
            if legal is None:
                return cannot(kbps)
            rate, sample_rate = legal
            channels = 1 if rate < MONO_BELOW_KBPS else sound.channels
            candidate = os.path.join(work, "try%d%s" % (number, extension))
            size = _encode(sound, extension, rate, sample_rate, channels,
                           candidate)
            tried.append("%d kbps %s -> %d bytes" % (
                rate, "mono" if channels == 1 else "stereo", size))
            if size <= limit:
                written = commit(candidate, out_path, limit)
                return Shrunk(
                    path=out_path, size=written, limit=limit,
                    summary="%s %d kbps %s%s" % (
                        CODECS[extension][0], rate,
                        "mono" if channels == 1 else "stereo",
                        ", %d Hz" % sample_rate if sample_rate else ""),
                    attempts=tuple(tried))
            # Missed: scale by how far, and always step at least one below.
            kbps = min(rate * limit / float(size) * 0.98, rate - 1)
        return CannotFit(
            limit=limit, smallest_honest_bytes=size,
            reason="%d encodes all came out over the limit" % attempts)
    finally:
        discard(work)


def _clock(seconds: float) -> str:
    whole = int(round(seconds))
    return "%d:%02d" % (whole // 60, whole % 60)
