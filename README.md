# downsize+

[![tests](https://github.com/Sanixaa23/downsizeplus/actions/workflows/test.yml/badge.svg)](https://github.com/Sanixaa23/downsizeplus/actions/workflows/test.yml)

Fits a video, GIF, photo or recording under an upload limit — Discord's
10 MB, WhatsApp's 16 MB, any size you name — and never hands you a file that
is over it.

```bash
pip install "downsize-plus[ffmpeg] @ git+https://github.com/Sanixaa23/downsizeplus"
downsize clip.mp4
downsize photo.jpg --size 800KB
downsize anim.gif --size 200KB
downsize voice.wav --for email
```

```
big.mp4  84.09 MB  1920x1080  60fps  0:30
target    discord (Discord free tier, checked 2026-10), 10.00 MB (10,000,000 bytes)

plan      960x540  60fps  video 2469 kbps  audio 128 kbps   (downscaled)
attempt   2469 kbps  ->  9,746,737 bytes  fits
wrote     big.downsized.mp4  9.75 MB   (253,263 bytes under the limit)
quality   SSIM 0.9683 (15.0 dB)  good; fine detail softened
```

That is a real run on a 30-second 1080p60 test pattern, nine seconds on a
laptop. The command is `downsize`, because `+` is not allowed in a Python
package or a repository name.

## Every kind of file, one promise

The kind of file decides how it is shrunk. It does not change what is
promised about the result: every candidate is a real encode, measured, and
only a measured fit is ever put at the output path.

| Input | Becomes | Searched over |
|---|---|---|
| MP4, MOV, MKV, WebM, AVI, … | H.264 MP4 | bitrate, resolution, frame rate |
| GIF | GIF (or MP4 with `-o NAME.mp4`) | size, frame rate, palette |
| JPEG | JPEG | quality, then resolution |
| PNG, WebP, BMP, TIFF, AVIF, HEIC | WebP (or JPEG with `-o NAME.jpg`) | quality, then resolution |
| WAV, FLAC, AIFF, WMA, AMR, MP3 | MP3 | bitrate, mono below 64 kbps |
| M4A, AAC | M4A | bitrate |
| Ogg, Opus | Opus | bitrate |

These are real runs on files generated for the tests — a 4000×3000 photo
of shapes and noise, the same with transparency, an 8-second animated test
pattern, and three minutes of noise and tone:

```
$ downsize photo.jpg --size 800KB
photo.jpg  8.89 MB  image
target    800KB, 800.0 KB (800,000 bytes)
attempt   4000x3000 q60 -> 1912845 bytes
attempt   3200x2400 q60 -> 1070812 bytes
attempt   2560x1920 q60 -> 656947 bytes
attempt   2560x1920 q78 -> 1019980 bytes
attempt   2560x1920 q69 -> 789889 bytes
attempt   2560x1920 q73 -> 873033 bytes
attempt   2560x1920 q71 -> 832727 bytes
attempt   2560x1920 q70 -> 812717 bytes
wrote     photo.downsized.jpg  789.9 KB   (10,111 bytes under the limit)
          JPEG quality 69, 4000x3000 -> 2560x1920

$ downsize logo.png --size 400KB
logo.png  20.70 MB  image
target    400KB, 400.0 KB (400,000 bytes)
attempt   4000x3000 q60 -> 2702302 bytes
attempt   3200x2400 q60 -> 1319230 bytes
attempt   2560x1920 q60 -> 720148 bytes
attempt   2048x1536 q60 -> 397030 bytes
attempt   2048x1536 q78 -> 598870 bytes
attempt   2048x1536 q69 -> 464522 bytes
attempt   2048x1536 q64 -> 414004 bytes
attempt   2048x1536 q62 -> 403922 bytes
attempt   2048x1536 q61 -> 403738 bytes
wrote     logo.downsized.webp  397.0 KB   (2,970 bytes under the limit)
          WEBP quality 60, 4000x3000 -> 2048x1536

$ downsize anim.gif --size 200KB
anim.gif  1.68 MB  GIF 480x270  20fps  0:08
target    200KB, 200.0 KB (200,000 bytes)
attempt   25% size, 10 fps, 32 colours -> 84582 bytes
attempt   60% size, 15 fps, 32 colours -> 354844 bytes
attempt   33% size, 15 fps, 128 colours -> 268652 bytes
attempt   33% size, 12 fps, 64 colours -> 174074 bytes
attempt   40% size, 12 fps, 32 colours -> 172311 bytes
attempt   33% size, 20 fps, 32 colours -> 212118 bytes
attempt   33% size, 15 fps, 64 colours -> 207657 bytes
attempt   40% size, 10 fps, 64 colours -> 185387 bytes
wrote     anim.downsized.gif  185.4 KB   (14,613 bytes under the limit)
          GIF at 40% size, 10 fps, 64 colours

$ downsize talk.wav --size 300KB -o voice.mp3
talk.wav  31.75 MB  audio pcm_s16le  stereo  3:00
target    300KB, 300.0 KB (300,000 bytes)

downsize: cannot fit this honestly: 3:00 of audio in this limit is 12.7 kbps, under the 16 kbps floor for .mp3 (Opus goes down to 8 kbps: -o NAME.opus).
The smallest it could reach is about 375.5 KB. Nothing was written.

$ downsize talk.wav --size 300KB -o voice.opus
talk.wav  31.75 MB  audio pcm_s16le  stereo  3:00
target    300KB, 300.0 KB (300,000 bytes)
attempt   12 kbps mono -> 224958 bytes
wrote     voice.opus  225.0 KB   (75,042 bytes under the limit)
          libopus 12 kbps mono
```

What each kind does, in short:

- **Images give up pixels before quality.** At quality 30 a JPEG is visibly
  made of 8×8 blocks; the same photo at 80% of the width and quality 70 just
  looks like a smaller photo. So the picture shrinks in 0.8 steps until
  quality 60 fits, then the quality is binary-searched upward. Only at the
  smallest allowed picture (320 px on the long side) may it drop toward 30.
  Transparency is kept by writing WebP. Rotation flags are applied to the
  pixels, and **all other metadata is dropped — including GPS location**.
  Animated WebP and APNG are refused rather than flattened to one frame.
  HEIC needs `pip install "downsize-plus[heic]"`.
- **GIFs stay GIFs.** There is no bitrate to compute, so about a hundred
  combinations of size, frame rate and palette are ordered from best-looking
  to smallest by a rough estimate, and binary-searched by real encodes —
  about eight. The estimate only picks what to try; the answer is the best
  setting that was measured under the limit. An MP4 of the same animation is
  usually far smaller: name the output `.mp4` to get one.
- **Audio gets its bitrate from the limit**, is measured, and is redone lower
  if it came out over. MP3 is constant-bitrate at rates LAME can really
  write. Below 64 kbps it is mono. Under 16 kbps for MP3 and AAC, or 8 for
  Opus, speech stops being intelligible, so nothing is written and the
  message says what would fit. Cover art is dropped; tags are kept.

## The promise, and how it is kept

Every compressor aims at a size. Two-pass x264 lands within a percent or two
of the requested bitrate on most footage and misses by more on some — static
screens, hard cuts, short clips where the container is a large share of the
file. "Usually under" is not good enough when the upload fails outright at
one byte over.

So the size is **measured, not estimated**. After every encode the result is
weighed. If it is over the limit, the video bitrate is scaled down by exactly
the overshoot and the clip is encoded again. If it is still over after the
last attempt, the candidate is deleted and the program says how close it got.

The output path is only ever written by renaming a file that was already
measured under the limit. There is no moment at which an oversize file sits
where you asked for the result, and an existing file at that path is left
alone if the run fails. Tests drive the retry loop through every branch with
an encoder that writes files of chosen sizes — including an overshoot on
every attempt — and check what is on disk afterwards.

## What it will not do

- **Re-encode a file that already fits.** That only throws quality away; it
  says so and writes nothing.
- **Scale anything up.**
- **Produce a smear that technically fits.** When even 240 lines cannot reach
  a watchable bits-per-pixel, it refuses and tells you the smallest limit it
  could honestly reach.
- **Replace the input**, or overwrite an existing output unless you pass
  `--overwrite`.

## Where the bits go

When the budget is tight, a fixed bitrate spread over fewer pixels looks
better than the same bitrate over more. The plan steps down in an order
chosen to lose the least:

1. source resolution and frame rate
2. smaller resolutions down to 480 lines, keeping the frame rate
3. 30 fps if the source is faster, from the top again
4. smaller still, down to 240 lines

The thresholds (0.045 bits per pixel for comfortable, 0.012 as the floor) are
mine, not a standard's, and they are arguments to `budget.plan` so anyone who
disagrees can move them.

Container overhead is estimated per frame and per audio packet rather than as
a flat percentage: a ten-minute 60 fps clip spends several hundred kilobytes
on its index, more than 3% of a 10 MB limit, while a five-second clip spends
almost nothing.

## What it cost, as a number

`quality` is SSIM — the structural similarity of each output frame to the
matching source frame, from ffmpeg's own `ssim` filter. It is measured the
way you will see it: a downscaled output is scaled back up to the source size
before comparing, so the resolution given up counts as loss. The words beside
the number are a reading aid and mine; the number is the measurement.

## Usage

```
downsize INPUT [-o OUTPUT]
  --for discord|discord-basic|whatsapp|email     (default: discord)
  --size 25MB                                    any limit; MB = 10^6, MiB = 2^20
  --attempts 4                                   encodes before giving up
  --overwrite                                    replace an existing output
  --image-format jpeg|webp                       force an image format
  --no-quality                                   skip the video SSIM measurement
  --json
```

The default output is `NAME.downsized.EXT` next to the input. For audio and
images, the extension of `-o` picks the format.

Exit code `0` means the output is under the limit, or the input already was.
`2` means nothing was written.

A limit written in MB is the safe one to aim at: when a service says "10 MB"
without saying which kind, 10,000,000 bytes is under both readings.

## Development

```bash
pip install -e ".[dev,ffmpeg]"
python -m pytest -q          # 47 tests
```

Every test input is generated: clips and GIFs from ffmpeg's `testsrc2`
pattern — moving detail and hard edges, close to the worst case for a fixed
bitrate — recordings from its noise and tone sources, and photos drawn with
Pillow. Nothing copyrighted is needed to run them.

## Licence

MIT.
