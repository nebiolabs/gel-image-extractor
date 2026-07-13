"""Image loading and grayscale/signal normalization."""

from pathlib import Path

import numpy as np
from skimage import color, io


def load_image(path: Path | str) -> np.ndarray:
    """Load an image file (TIFF/PNG/JPG) as a 2D grayscale float64 array.

    Preserves the source's native dynamic range (e.g. 16-bit TIFFs) rather
    than collapsing to 8-bit. Color images are converted to grayscale via
    standard luminosity weighting.
    """
    image = io.imread(str(path))
    if image.ndim == 3:
        # Drop an alpha channel if present, then collapse color to grayscale.
        image = image[..., :3]
        image = color.rgb2gray(image)
    return image.astype(np.float64)


def to_signal(image: np.ndarray, invert: bool = True) -> np.ndarray:
    """Normalize a raw grayscale image to a [0, 1] array where higher = more signal.

    `invert=True` (the default) is for dark-bands-on-light-background gels
    (e.g. Coomassie-stained SDS-PAGE, our purity workflow's only supported
    stain type so far) — raw pixel value is high for background and low for
    a band, so it's flipped so a band is a high-signal peak instead.
    """
    lo, hi = image.min(), image.max()
    if hi == lo:
        return np.zeros_like(image)
    normalized = (image - lo) / (hi - lo)
    return 1.0 - normalized if invert else normalized
