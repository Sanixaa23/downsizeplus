"""Images, GIFs and audio, each under the same promise as video.

Every input is generated here: photos and screenshots drawn with Pillow,
GIFs and recordings made by ffmpeg's own test sources. The checks are the
same for every kind: the file written is under the limit, it is still the
same kind of file, and a limit that cannot be met writes nothing.
"""
import os
import random
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from downsize_plus import audio, cli, gif, image, kinds, place  # noqa: E402
from downsize_plus.probe import FfmpegMissing, ffmpeg, probe  # noqa: E402

try:
    FFMPEG = ffmpeg()
except FfmpegMissing:
    FFMPEG = None

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")
PIL = pytest.importorskip("PIL.Image")


def run(*command):
    subprocess.run([FFMPEG, "-v", "error", "-y"] + list(command), check=True)


def photo(path, size=(3000, 2000), alpha=False, seed=1):
    """Busy enough to be expensive, like a real photo: shapes plus noise."""
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    picture = Image.new("RGB", size)
    draw = ImageDraw.Draw(picture)
    for _ in range(1500):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        draw.ellipse((x, y, x + rng.randrange(5, 150), y + rng.randrange(5, 150)),
                     fill=tuple(rng.randrange(256) for _ in range(3)))
    noise = Image.effect_noise(size, 40).convert("RGB")
    picture = Image.blend(picture, noise, 0.3)
    if alpha:
        picture = picture.convert("RGBA")
        picture.putalpha(180)
    picture.save(str(path), **({"quality": 97} if str(path).endswith(".jpg")
                               else {}))
    return path


# -- images -----------------------------------------------------------------

def test_a_jpeg_fits_and_stays_jpeg(tmp_path):
    src = photo(tmp_path / "big.jpg")
    out = tmp_path / "small.jpg"
    assert cli.main([str(src), "--size", "400KB", "-o", str(out)]) == cli.OK
    assert out.stat().st_size <= 400_000
    with PIL.open(str(out)) as written:
        assert written.format == "JPEG"


def test_pixels_go_before_quality(tmp_path):
    # At this limit, quality 30 at full size would fit; the search should
    # prefer a smaller picture at quality 60 or more instead.
    src = photo(tmp_path / "big.jpg")
    result = image.shrink(str(src), 500_000)
    assert isinstance(result, place.Shrunk)
    quality = int(result.summary.split("quality ")[1].split(",")[0])
    assert quality >= image.QUALITY_GOOD


def test_transparency_survives_as_webp(tmp_path):
    src = photo(tmp_path / "logo.png", size=(1600, 1200), alpha=True)
    result = image.shrink(str(src), 200_000)
    assert isinstance(result, place.Shrunk)
    assert result.path.endswith(".webp")
    with PIL.open(result.path) as written:
        assert written.mode == "RGBA"
    assert os.path.getsize(result.path) <= 200_000


def test_rotation_is_applied_and_metadata_dropped(tmp_path):
    from PIL import Image

    src = tmp_path / "sideways.jpg"
    picture = Image.effect_noise((1200, 800), 60).convert("RGB")
    exif = Image.Exif()
    exif[0x0112] = 6            # "rotate 90 clockwise to display"
    exif[0x010F] = "SomeCamera"
    picture.save(str(src), quality=98, exif=exif.tobytes())
    result = image.shrink(str(src), os.path.getsize(str(src)) // 2)
    with Image.open(result.path) as written:
        assert written.size == (800, 1200)      # upright now
        assert not written.getexif()


def test_an_impossible_image_limit_writes_nothing(tmp_path):
    src = photo(tmp_path / "big.jpg")
    out = tmp_path / "never.jpg"
    assert cli.main([str(src), "--size", "2KB", "-o", str(out)]) == \
        cli.NOTHING_WRITTEN
    assert not out.exists()


def test_an_animated_still_format_is_refused_not_flattened(tmp_path):
    from PIL import Image

    src = tmp_path / "moving.webp"
    frames = [Image.effect_noise((400, 300), 80).convert("RGB")
              for _ in range(3)]
    frames[0].save(str(src), save_all=True, append_images=frames[1:],
                   lossless=True)
    with pytest.raises(image.Unreadable):
        image.shrink(str(src), 1000)


# -- GIFs -------------------------------------------------------------------

@pytest.fixture(scope="module")
def animation(tmp_path_factory):
    if FFMPEG is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path_factory.mktemp("gif") / "anim.gif"
    # Odd sides on purpose: GIFs often have them, and H.264 cannot.
    run("-f", "lavfi", "-i", "testsrc2=size=481x271:rate=20:duration=6",
        "-vf", "split[a][b];[a]palettegen[p];[b][p]paletteuse", str(path))
    return path


@needs_ffmpeg
def test_a_gif_fits_and_stays_an_animated_gif(animation, tmp_path):
    out = tmp_path / "small.gif"
    limit = os.path.getsize(str(animation)) // 4
    assert cli.main([str(animation), "--size", "%dB" % limit, "-o",
                     str(out)]) == cli.OK
    assert out.stat().st_size <= limit
    with PIL.open(str(out)) as written:
        assert written.format == "GIF"
        assert written.n_frames > 10


def test_the_gif_ladder_is_best_first():
    fake = type("M", (), {"fps": 20.0, "width": 480, "height": 270})()
    steps = gif.ladder(fake)
    assert steps[0].scale == 1.0 and steps[0].colours == 256
    assert all(a.expected >= b.expected for a, b in zip(steps, steps[1:]))


@needs_ffmpeg
def test_an_impossible_gif_limit_writes_nothing(animation, tmp_path):
    out = tmp_path / "never.gif"
    assert cli.main([str(animation), "--size", "3KB", "-o", str(out)]) == \
        cli.NOTHING_WRITTEN
    assert not out.exists()


@needs_ffmpeg
def test_a_gif_can_become_a_video(animation, tmp_path):
    out = tmp_path / "anim.mp4"
    assert cli.main([str(animation), "--size", "200KB", "-o", str(out),
                     "--no-quality"]) == cli.OK
    assert out.stat().st_size <= 200_000
    assert probe(str(out)).video_codec == "h264"


# -- audio ------------------------------------------------------------------

@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    if FFMPEG is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path_factory.mktemp("audio") / "talk.wav"
    run("-f", "lavfi", "-i", "anoisesrc=d=60:c=pink:a=0.3",
        "-f", "lavfi", "-i", "sine=f=440:d=60",
        "-filter_complex", "[0][1]amerge=inputs=2", "-ar", "44100", str(path))
    return path


@needs_ffmpeg
@pytest.mark.parametrize("extension,limit", [
    (".mp3", 600_000), (".m4a", 400_000), (".opus", 150_000)])
def test_audio_fits_in_each_codec(recording, tmp_path, extension, limit):
    out = tmp_path / ("small" + extension)
    assert cli.main([str(recording), "--size", "%dB" % limit, "-o",
                     str(out)]) == cli.OK
    assert out.stat().st_size <= limit
    written = audio.probe_audio(str(out))
    assert abs(written.duration - 60.0) < 0.5


@needs_ffmpeg
def test_low_rates_are_mono(recording, tmp_path):
    result = audio.shrink(audio.probe_audio(str(recording)), 250_000,
                          str(tmp_path / "small.mp3"))
    assert isinstance(result, place.Shrunk)
    assert "mono" in result.summary


@needs_ffmpeg
def test_under_the_floor_writes_nothing_and_says_what_would(recording,
                                                            tmp_path, capsys):
    out = tmp_path / "never.mp3"
    assert cli.main([str(recording), "--size", "50KB", "-o", str(out)]) == \
        cli.NOTHING_WRITTEN
    assert not out.exists()
    assert "floor" in capsys.readouterr().err


def test_mp3_rates_are_ones_lame_can_write():
    for want in (17, 23, 31, 47, 63, 64, 100, 400):
        rate, _ = audio.legal_rate(".mp3", want)
        assert rate <= want
        assert rate in audio.MP3_RATES_44K + audio.MP3_RATES_LOW


# -- routing ----------------------------------------------------------------

@needs_ffmpeg
def test_a_song_with_cover_art_is_audio_not_video(tmp_path):
    cover = tmp_path / "cover.jpg"
    photo(cover, size=(300, 300))
    song = tmp_path / "song.bin"     # no telling extension: ffmpeg decides
    run("-f", "lavfi", "-i", "sine=f=330:d=5", "-i", str(cover),
        "-map", "0", "-map", "1", "-c:a", "libmp3lame", "-c:v", "mjpeg",
        "-disposition:v", "attached_pic", "-f", "mp3", str(song))
    assert kinds.kind_of(str(song)) == kinds.AUDIO


def test_a_mismatched_output_name_is_refused(tmp_path):
    src = photo(tmp_path / "a.jpg", size=(400, 300))
    with pytest.raises(ValueError):
        kinds.kind_of(str(src), str(tmp_path / "a.mp3"))


def test_commit_refuses_a_candidate_over_the_limit(tmp_path):
    candidate = tmp_path / "c.bin"
    candidate.write_bytes(b"x" * 101)
    with pytest.raises(place.TooBig):
        place.commit(str(candidate), str(tmp_path / "out.bin"), 100)
    assert not (tmp_path / "out.bin").exists()
