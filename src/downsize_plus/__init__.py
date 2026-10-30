"""downsize+ -- fit a video under an upload limit, and prove it fits.

One promise, kept by measurement rather than by estimate: the file written is
under the limit. Every encode is weighed afterwards; an overshoot is
re-encoded lower; a clip that cannot fit is refused with the smallest size it
could honestly reach, and nothing is written. What the shrinking cost is
printed as a measured SSIM against the original, not described.
"""
from __future__ import annotations

__version__ = "0.1.0"
