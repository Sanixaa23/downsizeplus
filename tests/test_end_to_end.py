"""Real encodes, on clips generated here so nothing copyrighted is needed.

ffmpeg's `testsrc2` pattern is full of moving detail and hard edges, which is
close to the worst case for a fixed bitrate -- a fair test of whether the
size promise holds where the encoder struggles most.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from downsize_plus import cli  # noqa: E402
from downsize_plus.probe import FfmpegMissing, ffmpeg, probe  # noqa: E402

try:
    FFMPEG = ffmpeg()
except FfmpegMissing:
    FFMPEG = None

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")


def make_clip(path, seconds=6, size="1280x720", rate=30, audio=True):
    command = [FFMPEG, "-v", "error", "-y", "-f", "lavfi",
               "-i", "testsrc2=size=%s:rate=%d:duration=%d" % (size, rate, seconds)]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=330:duration=%d" % seconds,
                    "-c:a", "aac", "-b:a", "192k", "-shortest"]
    command += ["-c:v", "libx264", "-crf", "14", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True)
    return path


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    return make_clip(tmp_path_factory.mktemp("src") / "clip.mp4")


def test_the_output_is_under_the_limit_and_measured(clip, tmp_path):
    out = tmp_path / "small.mp4"
    code = cli.main([str(clip), "--size", "600KB", "-o", str(out)])
    assert code == cli.OK
    assert out.exists()
    assert out.stat().st_size <= 600_000
    # And it is still a playable video of the same length.
    shrunk = probe(str(out))
    assert abs(shrunk.duration - 6.0) < 0.2


def test_a_clip_with_no_audio(tmp_path):
    src = make_clip(tmp_path / "silent.mp4", audio=False)
    out = tmp_path / "small.mp4"
    assert cli.main([str(src), "--size", "400KB", "-o", str(out)]) == cli.OK
    assert out.stat().st_size <= 400_000
    assert probe(str(out)).has_audio is False


def test_an_impossible_limit_writes_nothing(clip, tmp_path):
    out = tmp_path / "never.mp4"
    code = cli.main([str(clip), "--size", "5KB", "-o", str(out)])
    assert code == cli.NOTHING_WRITTEN
    assert not out.exists()


def test_a_file_that_already_fits_is_not_reencoded(clip, tmp_path):
    out = tmp_path / "unneeded.mp4"
    code = cli.main([str(clip), "--size", "500MB", "-o", str(out)])
    assert code == cli.OK
    assert not out.exists()


def test_a_missing_input_says_so(tmp_path, capsys):
    code = cli.main([str(tmp_path / "nope.mp4")])
    assert code == cli.NOTHING_WRITTEN
    assert "no such file" in capsys.readouterr().err
