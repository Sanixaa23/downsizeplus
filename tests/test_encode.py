"""The promise: the output path only ever holds a file under the limit.

The encoder is replaced with one that writes files of chosen sizes, so the
retry loop can be driven through every branch without ffmpeg -- including
the ones real footage rarely reaches, which are exactly the ones that matter.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

import pytest  # noqa: E402

from downsize_plus import budget, encode  # noqa: E402
from downsize_plus.probe import Media  # noqa: E402

LIMIT = 1_000_000


def plan(tmp_path):
    m = Media(path=str(tmp_path / "in.mp4"), size=5_000_000, duration=10.0,
              width=1280, height=720, fps=30.0, video_codec="h264",
              has_audio=True, audio_codec="aac")
    (tmp_path / "in.mp4").write_bytes(b"x")
    p = budget.plan(m, LIMIT)
    assert isinstance(p, budget.Plan)
    return m, p


def fake_encoder(monkeypatch, sizes):
    """Each encode writes the next size in `sizes`."""
    calls = []

    def two_pass(media, plan, video_kbps, work, candidate):
        calls.append(video_kbps)
        with open(candidate, "wb") as handle:
            handle.write(b"\0" * sizes[len(calls) - 1])

    monkeypatch.setattr(encode, "_two_pass", two_pass)
    return calls


def test_a_first_encode_that_fits_is_written(tmp_path, monkeypatch):
    m, p = plan(tmp_path)
    calls = fake_encoder(monkeypatch, [LIMIT - 10])
    out = tmp_path / "out.mp4"
    result = encode.encode(m, p, str(out))
    assert out.stat().st_size == LIMIT - 10
    assert len(calls) == 1 and result.headroom == 10


def test_an_overshoot_is_retried_lower_and_only_the_fit_is_written(
        tmp_path, monkeypatch):
    m, p = plan(tmp_path)
    calls = fake_encoder(monkeypatch, [LIMIT + 50_000, LIMIT - 1])
    out = tmp_path / "out.mp4"
    result = encode.encode(m, p, str(out))
    assert len(calls) == 2
    assert calls[1] < calls[0], "the retry must aim lower"
    assert out.stat().st_size == LIMIT - 1
    assert [a.fits for a in result.attempts] == [False, True]


def test_exactly_at_the_limit_fits(tmp_path, monkeypatch):
    m, p = plan(tmp_path)
    fake_encoder(monkeypatch, [LIMIT])
    out = tmp_path / "out.mp4"
    encode.encode(m, p, str(out))
    assert out.stat().st_size == LIMIT


def test_when_nothing_fits_nothing_is_written(tmp_path, monkeypatch):
    m, p = plan(tmp_path)
    fake_encoder(monkeypatch, [LIMIT + 9] * 4)
    out = tmp_path / "out.mp4"
    with pytest.raises(encode.DidNotFit) as caught:
        encode.encode(m, p, str(out), attempts=4)
    assert not out.exists()
    assert caught.value.best_size == LIMIT + 9


def test_a_failed_run_leaves_an_existing_output_untouched(
        tmp_path, monkeypatch):
    m, p = plan(tmp_path)
    fake_encoder(monkeypatch, [LIMIT + 9] * 4)
    out = tmp_path / "out.mp4"
    out.write_bytes(b"precious")
    with pytest.raises(encode.DidNotFit):
        encode.encode(m, p, str(out), overwrite=True)
    assert out.read_bytes() == b"precious"


def test_an_existing_output_is_not_overwritten_unless_asked(
        tmp_path, monkeypatch):
    m, p = plan(tmp_path)
    fake_encoder(monkeypatch, [10])
    out = tmp_path / "out.mp4"
    out.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        encode.encode(m, p, str(out))
    assert out.read_bytes() == b"keep"


def test_the_input_is_never_the_output(tmp_path, monkeypatch):
    m, p = plan(tmp_path)
    fake_encoder(monkeypatch, [10])
    with pytest.raises(ValueError):
        encode.encode(m, p, m.path, overwrite=True)
    assert (tmp_path / "in.mp4").read_bytes() == b"x"
