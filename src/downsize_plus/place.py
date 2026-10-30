"""The promise, in one place, for every kind of file.

Video, images, GIFs and audio are shrunk four different ways, and they share
exactly one rule: the output path is only ever written by renaming a file
that has already been measured under the limit. It lives here so that rule
cannot drift between them -- four copies of it would be four chances for
one to forget the measurement.

Also here, the two refusals about the user's own files: the input is never
the output, and an existing output is never replaced unless asked.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import NamedTuple, Tuple


class TooBig(RuntimeError):
    """A candidate was handed to `commit` without fitting. A bug, not a
    user error: callers are meant to measure first."""


class AlreadyFits(NamedTuple):
    size: int
    limit: int


class CannotFit(NamedTuple):
    limit: int
    smallest_honest_bytes: int
    reason: str


class Shrunk(NamedTuple):
    path: str
    size: int
    limit: int
    summary: str               # what was done, in one line
    attempts: Tuple[str, ...]  # one line per encode tried

    @property
    def headroom(self) -> int:
        return self.limit - self.size


def same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == \
            os.path.normcase(os.path.abspath(b))


def guard(source: str, out_path: str, overwrite: bool) -> None:
    if same_file(source, out_path):
        raise ValueError("the output would replace the input; choose "
                         "another name")
    if os.path.exists(out_path) and not overwrite:
        raise FileExistsError("%s already exists; pass --overwrite to "
                              "replace it" % out_path)


def workspace() -> str:
    return tempfile.mkdtemp(prefix="downsize-")


def commit(candidate: str, out_path: str, limit: int) -> int:
    """Measure `candidate` and, only if it fits, move it to `out_path`.

    The measurement is repeated here even when the caller has just done it,
    because this is the last moment before the file becomes the result.
    """
    size = os.path.getsize(candidate)
    if size > limit:
        raise TooBig("%d bytes is over the %d-byte limit" % (size, limit))
    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)
    shutil.move(candidate, out_path)
    return size


def discard(directory: str) -> None:
    shutil.rmtree(directory, ignore_errors=True)
