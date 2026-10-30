"""Which of the four ways a file is shrunk.

The extension decides first, because it is what the person sees and what
the place they upload to will look at. ffmpeg decides the rest: a file with
a real video stream is video, and a file with only sound is audio.

"Real" matters. An MP3 with album art reports a video stream -- the cover,
one still frame marked "(attached pic)" -- and treating it as a video would
turn a song into a slideshow MP4. So attached pictures do not count.

An output name can change the kind: `downsize anim.gif -o anim.mp4` asks for
the GIF as a video, which is usually a tenth of the size.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .audio import AUDIO_EXTENSIONS
from .image import IMAGE_EXTENSIONS
from .probe import Unreadable, describe

IMAGE = "image"
GIF = "gif"
AUDIO = "audio"
VIDEO = "video"

VIDEO_OUTPUTS = (".mp4", ".m4v", ".mov")

_REAL_VIDEO = re.compile(r"Stream #\d+:\d+[^:]*: Video: (?![^\n]*attached pic)")
_ANY_AUDIO = re.compile(r"Stream #\d+:\d+[^:]*: Audio: ")


def kind_of(path: str, out_path: Optional[str] = None) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError("no such file: %s" % path)
    extension = os.path.splitext(path)[1].lower()
    wanted = os.path.splitext(out_path)[1].lower() if out_path else ""

    if extension in IMAGE_EXTENSIONS:
        kind = IMAGE
    elif extension == ".gif":
        kind = VIDEO if wanted in VIDEO_OUTPUTS else GIF
    elif extension in AUDIO_EXTENSIONS:
        kind = AUDIO
    else:
        report = describe(path)
        if _REAL_VIDEO.search(report):
            kind = VIDEO
        elif _ANY_AUDIO.search(report):
            kind = AUDIO
        else:
            raise Unreadable("%s: not an image, GIF, audio or video file "
                             "that ffmpeg can read" % os.path.basename(path))

    if kind == VIDEO and wanted and wanted not in VIDEO_OUTPUTS:
        raise ValueError("video is written as MP4; name the output %s"
                         % " or ".join(VIDEO_OUTPUTS))
    if kind == GIF and wanted and wanted != ".gif":
        raise ValueError("a GIF stays a GIF (name it .gif), or becomes a "
                         "video (name it .mp4)")
    if kind == IMAGE and wanted and wanted not in (".jpg", ".jpeg", ".webp"):
        raise ValueError("images are written as JPEG or WebP; name the "
                         "output .jpg or .webp")
    return kind
