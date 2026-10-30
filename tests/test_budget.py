"""The plan, as arithmetic. No ffmpeg, no files."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

import pytest  # noqa: E402

from downsize_plus import budget  # noqa: E402
from downsize_plus.probe import Media  # noqa: E402


def media(size=80_000_000, duration=30.0, w=1920, h=1080, fps=60.0,
          audio=True):
    return Media(path="x.mp4", size=size, duration=duration, width=w,
                 height=h, fps=fps, video_codec="h264", has_audio=audio,
                 audio_codec="aac" if audio else None)


def test_a_file_that_already_fits_is_left_alone():
    out = budget.plan(media(size=5_000_000), 10_000_000)
    assert isinstance(out, budget.AlreadyFits)


def test_the_streams_never_get_the_whole_limit():
    p = budget.plan(media(), 10_000_000)
    assert isinstance(p, budget.Plan)
    assert p.target_bytes < p.limit_bytes
    bits = (p.video_kbps + p.audio_kbps) * 1000 * p.duration
    assert bits / 8 <= p.target_bytes


def test_nothing_is_ever_scaled_up():
    p = budget.plan(media(w=640, h=360, size=50_000_000), 10_000_000)
    assert p.width <= 640 and p.height <= 360


def test_dimensions_are_even():
    p = budget.plan(media(w=1918, h=1078), 10_000_000)
    assert p.width % 2 == 0 and p.height % 2 == 0


def test_resolution_steps_down_before_frame_rate_does():
    p = budget.plan(media(), 10_000_000)
    assert p.downscaled and not p.fps_capped


def test_a_long_clip_caps_the_frame_rate_before_going_below_480():
    p = budget.plan(media(duration=300.0), 10_000_000)
    assert p.fps_capped


def test_an_impossible_target_is_refused_with_a_reachable_size():
    out = budget.plan(media(duration=3600.0), 1_000_000)
    assert isinstance(out, budget.CannotFit)
    # The suggestion must itself be plannable, or it is not a suggestion.
    retry = budget.plan(media(duration=3600.0), out.smallest_honest_bytes)
    assert isinstance(retry, budget.Plan)


def test_no_audio_spends_everything_on_video():
    with_audio = budget.plan(media(), 10_000_000)
    without = budget.plan(media(audio=False), 10_000_000)
    assert without.audio_kbps == 0
    assert without.video_kbps > with_audio.video_kbps


def test_overhead_grows_with_frames_not_with_the_limit():
    short = budget.overhead(5.0, 30.0, True)
    long_ = budget.overhead(600.0, 60.0, True)
    assert long_ > 10 * short


@pytest.mark.parametrize("text,expected", [
    ("10MB", 10_000_000), ("10 mb", 10_000_000), ("8MiB", 8 * 1024 * 1024),
    ("500KB", 500_000), ("1.5GB", 1_500_000_000), ("25", 25_000_000),
])
def test_sizes_parse(text, expected):
    assert budget.parse_size(text) == expected


def test_a_bad_size_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        budget.parse_size("ten megabytes")
    with pytest.raises(ValueError):
        budget.parse_size("0MB")
