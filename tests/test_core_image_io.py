import numpy as np

from gel_extractor.core.image_io import to_signal


def test_to_signal_inverts_dark_bands_to_high_signal():
    image = np.array([[1.0, 0.0], [0.5, 1.0]])
    signal = to_signal(image, invert=True)
    assert signal[0, 1] == 1.0  # darkest pixel -> max signal
    assert signal[0, 0] == 0.0  # lightest pixel -> min signal


def test_to_signal_uniform_image_returns_zeros():
    image = np.ones((5, 5))
    signal = to_signal(image)
    assert np.all(signal == 0)


def test_to_signal_no_invert_preserves_order():
    image = np.array([[0.0, 1.0]])
    signal = to_signal(image, invert=False)
    assert signal[0, 0] == 0.0
    assert signal[0, 1] == 1.0
