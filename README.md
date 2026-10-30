# downsize+

Fits a video under an upload limit — Discord's 10 MB, WhatsApp's 16 MB, any
size you name — and never hands you a file that is over it.

```bash
pip install "downsize-plus[ffmpeg] @ git+https://github.com/Sanixaa23/downsize-plus"
downsize clip.mp4
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
  --no-quality                                   skip the SSIM measurement
  --json
```

Exit code `0` means the output is under the limit, or the input already was.
`2` means nothing was written.

A limit written in MB is the safe one to aim at: when a service says "10 MB"
without saying which kind, 10,000,000 bytes is under both readings.

## Development

```bash
pip install -e ".[dev,ffmpeg]"
python -m pytest -q          # 28 tests
```

The end-to-end tests generate their own clips with ffmpeg's `testsrc2`
pattern — moving detail and hard edges, close to the worst case for a fixed
bitrate — so nothing copyrighted is needed to run them.

## Licence

MIT.
