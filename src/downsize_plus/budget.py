"""How many bits the output is allowed, and where to spend them.

Pure arithmetic, no ffmpeg. That is deliberate: this is the part that decides
whether the promise -- the output never exceeds the limit -- is even
attemptable, and it can be tested exhaustively without encoding anything.

The budget is the limit minus what the container costs. MP4 overhead is not
a percentage. It is a fixed header plus a small entry per frame and per audio
packet, so a ten-minute 60fps clip spends several hundred kilobytes on
bookkeeping -- more than a flat 3% of a 10MB limit -- while a five-second
clip spends almost nothing. Estimating it per frame keeps the first encode
close to the target. The retry loop in `encode.py` is what actually
guarantees the limit; a good estimate only decides how often it has to run.

Then quality. A fixed bitrate spread over too many pixels looks worse than
the same bitrate over fewer, so when bits per pixel drop below what x264
handles cleanly the plan steps down, in an order chosen to lose the least:

  1. the source resolution and frame rate
  2. smaller resolutions, down to 480 lines, at the source frame rate
  3. 30 frames per second, if the source is faster, from the top again
  4. smaller still, down to 240 lines

If even 240 lines cannot reach the floor, the plan says the clip cannot
honestly fit and reports the smallest limit it could reach, rather than
producing a smear of blocks that technically fits. Nothing is ever scaled up.

And if the file already fits, there is no plan: re-encoding a file that is
already small enough only throws quality away.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional, Sequence, Tuple

from .probe import Media

# Bits per pixel per frame. Above COMFORT x264 at medium preset holds up on
# ordinary footage; below FLOOR it is blocks. Both are arguments so anyone
# who disagrees can move them and see the plan change.
COMFORT_BPP = 0.045
FLOOR_BPP = 0.012

LADDER = (2160, 1440, 1080, 720, 540, 480, 360, 240)
COMFORT_FLOOR_HEIGHT = 480
FPS_CAP = 30.0

# Container bookkeeping: a fixed header, plus per-sample table entries.
OVERHEAD_FIXED = 16 * 1024
OVERHEAD_PER_VIDEO_FRAME = 16
OVERHEAD_PER_AUDIO_PACKET = 12
AAC_PACKETS_PER_SECOND = 48000 / 1024.0
# On top of the estimate, a slice the encoder's rate control may overshoot.
RATE_CONTROL_MARGIN = 0.02

AUDIO_TIERS = ((1000, 128), (500, 96), (250, 64), (0, 48))
MIN_VIDEO_KBPS = 80


class Plan(NamedTuple):
    limit_bytes: int
    target_bytes: int          # what the streams may use, overhead removed
    duration: float
    width: int
    height: int
    fps: float
    video_kbps: int
    audio_kbps: int            # 0 when the source has no audio
    bits_per_pixel: float
    downscaled: bool
    fps_capped: bool
    below_comfort: bool        # reaches the floor, not the comfort level

    @property
    def total_kbps(self) -> int:
        return self.video_kbps + self.audio_kbps


class AlreadyFits(NamedTuple):
    size: int
    limit_bytes: int


class CannotFit(NamedTuple):
    limit_bytes: int
    smallest_honest_bytes: int  # what 240 lines at the floor would need
    reason: str


def _even(value: float) -> int:
    """x264 with 4:2:0 chroma needs even dimensions."""
    return max(2, int(round(value / 2.0)) * 2)


def overhead(duration: float, fps: float, has_audio: bool) -> int:
    frames = duration * fps
    packets = duration * AAC_PACKETS_PER_SECOND if has_audio else 0
    return int(OVERHEAD_FIXED + frames * OVERHEAD_PER_VIDEO_FRAME
               + packets * OVERHEAD_PER_AUDIO_PACKET)


def audio_kbps_for(total_kbps: float) -> int:
    for threshold, kbps in AUDIO_TIERS:
        if total_kbps >= threshold:
            return kbps
    return AUDIO_TIERS[-1][1]


def _candidates(media: Media) -> List[Tuple[int, int, float, bool, bool]]:
    """(width, height, fps, downscaled, fps_capped) in degradation order."""
    source_h = media.height
    heights = [source_h] + [h for h in LADDER if h < source_h]
    out: List[Tuple[int, int, float, bool, bool]] = []

    def add(height: int, fps: float, capped: bool) -> None:
        width = _even(media.width * height / float(source_h))
        entry = (width, _even(height), fps, height != source_h, capped)
        if entry not in out:
            out.append(entry)

    upper = [h for h in heights if h >= COMFORT_FLOOR_HEIGHT] or [source_h]
    lower = [h for h in heights if h < COMFORT_FLOOR_HEIGHT]

    for h in upper:
        add(h, media.fps, False)
    capped_fps = min(media.fps, FPS_CAP)
    if capped_fps < media.fps:
        for h in upper:
            add(h, capped_fps, True)
    for h in lower:
        add(h, capped_fps, capped_fps < media.fps)
    return out


def plan(media: Media, limit_bytes: int,
         comfort_bpp: float = COMFORT_BPP,
         floor_bpp: float = FLOOR_BPP):
    """A Plan, or AlreadyFits, or CannotFit."""
    if limit_bytes <= 0:
        raise ValueError("the size limit must be positive")

    if media.size <= limit_bytes:
        return AlreadyFits(size=media.size, limit_bytes=limit_bytes)

    candidates = _candidates(media)
    best_floor: Optional[Plan] = None

    for width, height, fps, downscaled, capped in candidates:
        cost = overhead(media.duration, fps, media.has_audio)
        streams = int((limit_bytes - cost) * (1.0 - RATE_CONTROL_MARGIN))
        if streams <= 0:
            continue
        total_kbps = streams * 8.0 / media.duration / 1000.0
        audio = audio_kbps_for(total_kbps) if media.has_audio else 0
        video = int(total_kbps - audio)
        if video < MIN_VIDEO_KBPS:
            continue
        bpp = video * 1000.0 / (width * height * fps)

        candidate = Plan(
            limit_bytes=limit_bytes, target_bytes=streams,
            duration=media.duration, width=width, height=height, fps=fps,
            video_kbps=video, audio_kbps=audio, bits_per_pixel=bpp,
            downscaled=downscaled, fps_capped=capped,
            below_comfort=bpp < comfort_bpp)

        if bpp >= comfort_bpp:
            return candidate
        if bpp >= floor_bpp:
            # Keep descending: a smaller picture may still reach comfort.
            # If none does, the last one that cleared the floor is the best
            # available, because it has the most bits per pixel.
            best_floor = candidate

    if best_floor is not None:
        return best_floor

    return CannotFit(
        limit_bytes=limit_bytes,
        smallest_honest_bytes=smallest_honest(media, floor_bpp),
        reason="even at %d lines and %g fps there are not enough bits for "
               "a watchable picture" % (LADDER[-1], min(media.fps, FPS_CAP)))


def smallest_honest(media: Media, floor_bpp: float = FLOOR_BPP) -> int:
    """The smallest limit at which 240 lines would clear the floor."""
    height = min(LADDER[-1], media.height)
    width = _even(media.width * height / float(media.height))
    fps = min(media.fps, FPS_CAP)
    video_bps = max(floor_bpp * width * height * fps, MIN_VIDEO_KBPS * 1000.0)
    audio_bps = AUDIO_TIERS[-1][1] * 1000.0 if media.has_audio else 0.0
    streams = (video_bps + audio_bps) * media.duration / 8.0
    total = streams / (1.0 - RATE_CONTROL_MARGIN) \
        + overhead(media.duration, fps, media.has_audio)
    return int(total) + 1


def parse_size(text: str) -> int:
    """'10MB', '25 mb', '8MiB', '500KB' -> bytes.

    MB is 1,000,000 bytes and MiB is 1,048,576. When a service says "10 MB"
    without saying which, 10,000,000 is under both readings, so a limit
    written in MB is always the safe one to aim at.
    """
    cleaned = text.strip().replace(" ", "").lower()
    units = (("gib", 1024 ** 3), ("mib", 1024 ** 2), ("kib", 1024),
             ("gb", 10 ** 9), ("mb", 10 ** 6), ("kb", 10 ** 3), ("b", 1))
    for suffix, factor in units:
        if cleaned.endswith(suffix):
            number = cleaned[: -len(suffix)]
            break
    else:
        number, factor = cleaned, 10 ** 6
    try:
        value = float(number)
    except ValueError:
        raise ValueError("cannot read %r as a size; try 10MB or 25MiB" % text)
    if value <= 0:
        raise ValueError("a size limit must be positive")
    return int(value * factor)


# Upload limits people actually hit. Services change these, so each carries
# the date it was checked; --size always overrides.
PRESETS = {
    "discord": (parse_size("10MB"), "Discord free tier, checked 2026-10"),
    "discord-basic": (parse_size("50MB"), "Discord Nitro Basic, checked 2026-10"),
    "whatsapp": (parse_size("16MB"), "WhatsApp video message, checked 2026-10"),
    "email": (parse_size("20MB"), "a common mail attachment ceiling"),
}
