"""downsize+ on the command line.

    downsize clip.mp4                     # fits it under Discord's 10 MB
    downsize clip.mp4 --for whatsapp
    downsize clip.mp4 --size 25MB -o small.mp4

Two exit codes, and the second one is the promise:

    0   the output is under the limit (or the input already was)
    2   nothing was written

There is no code for "wrote something slightly too big", because that never
happens: the output path is only ever created by renaming a file that was
already measured under the limit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence

from . import __version__
from .budget import AlreadyFits, CannotFit, PRESETS, parse_size, plan
from .encode import DEFAULT_ATTEMPTS, Attempt, DidNotFit, EncodeFailed, encode
from .probe import FfmpegMissing, Unreadable, probe
from .quality import measure

OK = 0
NOTHING_WRITTEN = 2


def _make_output_printable() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _mb(size: int) -> str:
    return "%.2f MB" % (size / 1e6)


def _clock(seconds: float) -> str:
    whole = int(round(seconds))
    return "%d:%02d" % (whole // 60, whole % 60)


def _default_output(path: str) -> str:
    stem, _ = os.path.splitext(path)
    return stem + ".downsized.mp4"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="downsize",
        description="downsize+: fits a video under an upload limit, and "
                    "never hands you a file that is over it.")
    p.add_argument("input", help="the video to shrink")
    p.add_argument("-o", "--output",
                   help="where to write it (default: NAME.downsized.mp4)")
    target = p.add_mutually_exclusive_group()
    target.add_argument("--for", dest="preset", choices=sorted(PRESETS),
                        help="a known upload limit (default: discord)")
    target.add_argument("--size", help="an explicit limit, e.g. 25MB or 8MiB")
    p.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                   help="encodes to try before giving up (default: %d)"
                        % DEFAULT_ATTEMPTS)
    p.add_argument("--overwrite", action="store_true",
                   help="replace the output file if it exists")
    p.add_argument("--no-quality", action="store_true",
                   help="skip the SSIM measurement (it decodes both files)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    p.add_argument("--version", action="version",
                   version="downsize+ %s" % __version__)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    _make_output_printable()
    args = _parser().parse_args(argv)

    if args.size:
        try:
            limit = parse_size(args.size)
        except ValueError as error:
            print("downsize: %s" % error, file=sys.stderr)
            return NOTHING_WRITTEN
        limit_label = args.size
    else:
        name = args.preset or "discord"
        limit, note = PRESETS[name]
        limit_label = "%s (%s)" % (name, note)

    report = {"input": args.input, "limit_bytes": limit,
              "limit": limit_label, "written": None}
    say = (lambda *a: None) if args.json else print

    try:
        media = probe(args.input)
    except (FileNotFoundError, Unreadable, FfmpegMissing) as error:
        print("downsize: %s" % error, file=sys.stderr)
        return NOTHING_WRITTEN

    say("%s  %s  %dx%d  %gfps  %s%s" % (
        os.path.basename(media.path), _mb(media.size), media.width,
        media.height, media.fps, _clock(media.duration),
        "" if media.has_audio else "  no audio"))
    say("target    %s, %s (%s bytes)" % (limit_label, _mb(limit),
                                         format(limit, ",")))

    decided = plan(media, limit)

    if isinstance(decided, AlreadyFits):
        say("\nalready %s, under the limit. Nothing to do: re-encoding a file "
            "that fits only loses quality." % _mb(decided.size))
        report.update(already_fits=True, size=decided.size)
        if args.json:
            print(json.dumps(report, indent=1))
        return OK

    if isinstance(decided, CannotFit):
        print("\ndownsize: cannot fit this honestly. %s.\nThe smallest limit "
              "it could reach is about %s. Nothing was written."
              % (decided.reason, _mb(decided.smallest_honest_bytes)),
              file=sys.stderr)
        report.update(cannot_fit=True,
                      smallest_honest_bytes=decided.smallest_honest_bytes)
        if args.json:
            print(json.dumps(report, indent=1))
        return NOTHING_WRITTEN

    changes = []
    if decided.downscaled:
        changes.append("downscaled")
    if decided.fps_capped:
        changes.append("fps capped")
    say("\nplan      %dx%d  %gfps  video %d kbps  audio %s%s" % (
        decided.width, decided.height, decided.fps, decided.video_kbps,
        "%d kbps" % decided.audio_kbps if decided.audio_kbps else "none",
        "   (%s)" % ", ".join(changes) if changes else ""))
    if decided.below_comfort:
        say("          this is as small as it goes before the picture falls "
            "apart; expect it to look rough")

    def progress(attempt: Attempt) -> None:
        say("attempt   %d kbps  ->  %s bytes  %s" % (
            attempt.video_kbps, format(attempt.size, ","),
            "fits" if attempt.fits else "over by %s bytes, retrying lower"
            % format(attempt.size - limit, ",")))

    output = args.output or _default_output(args.input)
    try:
        result = encode(media, decided, output, attempts=args.attempts,
                        overwrite=args.overwrite, progress=progress)
    except DidNotFit as error:
        print("downsize: %s" % error, file=sys.stderr)
        return NOTHING_WRITTEN
    except (FileExistsError, ValueError, EncodeFailed) as error:
        print("downsize: %s" % error, file=sys.stderr)
        return NOTHING_WRITTEN

    say("wrote     %s  %s   (%s bytes under the limit)" % (
        result.path, _mb(result.size), format(result.headroom, ",")))
    report.update(written=result.path, size=result.size,
                  headroom=result.headroom,
                  attempts=[a._asdict() for a in result.attempts],
                  plan=decided._asdict())

    if not args.no_quality:
        try:
            quality = measure(media, result.path, decided)
            say("quality   SSIM %.4f%s  %s" % (
                quality.ssim,
                " (%.1f dB)" % quality.db if quality.db is not None else "",
                quality.reading))
            report["quality"] = quality._asdict()
        except RuntimeError as error:
            say("quality   not measured: %s" % error)

    if args.json:
        print(json.dumps(report, indent=1))
    return OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
