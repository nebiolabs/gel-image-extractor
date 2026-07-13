"""Ladder identification and MW calibration."""

from dataclasses import dataclass

import numpy as np

from gel_extractor.core.bands import correct_baseline, detect_bands

# Known ladder band sizes (kDa), sorted descending (highest MW first, since
# higher-MW bands migrate less and sit closer to the top of the gel).
#
# Intentionally empty for now: we could not verify NEB P7719's exact 11 band
# sizes from public product pages/spec sheets/protocols (only that it spans
# 10-250 kDa with orange/green reference bands at 72/26 kDa) -- see
# QUESTIONS_FOR_USERS.md. Shipping a guessed set of numbers as if verified
# would risk silently wrong purity calculations, so callers must supply
# --ladder-bands explicitly until a verified entry is added here.
KNOWN_LADDERS: dict[str, list[float]] = {}


class UnknownLadderError(ValueError):
    """Raised when a requested ladder name isn't in KNOWN_LADDERS."""


def get_ladder_bands(name: str) -> list[float]:
    """Look up known band MWs (kDa, descending) for a recognized ladder name."""
    try:
        return KNOWN_LADDERS[name]
    except KeyError:
        known = sorted(KNOWN_LADDERS) or "(none yet -- see QUESTIONS_FOR_USERS.md)"
        raise UnknownLadderError(
            f"Unrecognized ladder {name!r}. Known ladders: {known}. Use "
            "--ladder-bands to supply band sizes directly."
        ) from None


class LadderCalibrationError(ValueError):
    """Raised when a ladder lane's bands can't be matched to known MWs."""


@dataclass(frozen=True)
class LadderCalibration:
    """A fitted log10(MW) vs. migration-position calibration curve."""

    positions: np.ndarray
    mws: np.ndarray
    slope: float
    intercept: float

    def mw_at(self, position: float) -> float:
        """Estimate molecular weight (kDa) at a given migration position."""
        return float(10 ** (self.slope * position + self.intercept))


def calibrate_ladder(profile: np.ndarray, known_mws: list[float]) -> LadderCalibration:
    """Detect bands in a ladder lane's profile and fit a calibration curve.

    Assumes `known_mws` are matched top-to-bottom against detected bands
    (lowest pixel position = largest MW, since larger proteins migrate
    less). Raises `LadderCalibrationError` if the detected band count
    doesn't match the known band count, since a silent mismatch would
    miscalibrate every downstream purity result.
    """
    corrected = correct_baseline(profile)
    bands = detect_bands(corrected)

    if len(bands) != len(known_mws):
        raise LadderCalibrationError(
            f"Detected {len(bands)} band(s) in the ladder lane but "
            f"{len(known_mws)} known band size(s) were provided -- cannot "
            "reliably match them. Try adjusting detection sensitivity or "
            "double-check --ladder-bands."
        )

    ordered_bands = sorted(bands, key=lambda b: b.center)
    positions = np.array([b.center for b in ordered_bands])
    mws = np.array(sorted(known_mws, reverse=True))

    slope, intercept = np.polyfit(positions, np.log10(mws), 1)
    return LadderCalibration(positions=positions, mws=mws, slope=float(slope), intercept=float(intercept))
