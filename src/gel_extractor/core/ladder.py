"""Ladder identification and MW calibration."""

from dataclasses import dataclass

import numpy as np

from gel_extractor.core.bands import correct_baseline, detect_bands

# Known ladder band sizes (kDa), sorted descending (highest MW first, since
# higher-MW bands migrate less and sit closer to the top of the gel).
#
# P7719 (NEB Color Prestained Protein Standard, Broad Range, 10-250 kDa)
# verified 2026-07-13 against NEB's own labeled product gel image (11 bands;
# orange reference band at 72 kDa and green reference band at 26 kDa both
# match, confirming the source) -- see QUESTIONS_FOR_USERS.md for provenance.
KNOWN_LADDERS: dict[str, list[float]] = {
    "P7719": [250.0, 180.0, 130.0, 95.0, 72.0, 55.0, 43.0, 34.0, 26.0, 17.0, 10.0],
}


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
    r_squared: float

    def mw_at(self, position: float) -> float:
        """Estimate molecular weight (kDa) at a given migration position."""
        return float(10 ** (self.slope * position + self.intercept))


# A real bench scientist doesn't count every ladder rung -- they anchor off
# whichever 1-2 nearby rungs are clearly visible. Requiring an exact 1:1 match
# against every known band was stricter than the actual task and is why
# calibration failed on every real image tried (2026-07-13): SDS-PAGE
# migration is log-linear, which compresses (and often merges) high-MW bands
# long before it does low-MW ones -- a well-known, near-universal artifact,
# not random noise. So instead of requiring an exact count match, we now
# calibrate from whatever bands ARE confidently detected, with a minimum band
# count and a goodness-of-fit check so a bad guess still gets rejected rather
# than silently miscalibrating everything downstream.
MIN_MATCHED_BANDS = 3
# 0.9 was too strict in practice: on a real gel, a plausible alignment (2 of
# 11 known bands assumed undetected) fit at ~0.89. 0.85 admits that while
# still rejecting clearly-wrong fits (the poor-fit test case measures ~0.64).
MIN_R_SQUARED = 0.85


def calibrate_ladder(
    profile: np.ndarray,
    known_mws: list[float],
    min_matched_bands: int = MIN_MATCHED_BANDS,
    min_r_squared: float = MIN_R_SQUARED,
) -> LadderCalibration:
    """Detect bands in a ladder lane's profile and fit a calibration curve.

    Doesn't require detecting every known band. If fewer bands are detected
    than known sizes, some known bands must have gone undetected somewhere
    along the ladder -- but *where* isn't assumed. An earlier version of
    this function always assumed undetected bands were the highest-MW ones
    (SDS-PAGE resolves worst there), but real testing (2026-07-13) found a
    real image where the opposite alignment fit meaningfully better (R²
    0.95 vs. 0.89) and was independently corroborated by where the dominant
    band actually sat in a real sample lane -- so assuming a fixed direction
    is itself a real source of error. Instead: try every contiguous subset
    of `known_mws` of the detected length, fit each against the detected
    band positions, and keep whichever fits best. If more bands are detected
    than known sizes (likely noise), keeps only the most prominent ones (by
    area) before this search. Raises `LadderCalibrationError` if fewer than
    `min_matched_bands` are detected, or if even the best-fitting alignment
    is poor (R² below `min_r_squared`) -- both signal that no assumed
    correspondence can be trusted, so it's safer to refuse than to guess.
    """
    corrected = correct_baseline(profile)
    bands = detect_bands(corrected)

    if len(bands) < min_matched_bands:
        raise LadderCalibrationError(
            f"Only {len(bands)} band(s) detected in the ladder lane -- need at "
            f"least {min_matched_bands} to calibrate reliably. Try "
            "--ladder-bands with an explicit list, or --allow-heuristic."
        )

    k = min(len(bands), len(known_mws))
    ordered_bands = sorted(bands, key=lambda b: b.area, reverse=True)[:k]
    ordered_bands = sorted(ordered_bands, key=lambda b: b.center)
    positions = np.array([b.center for b in ordered_bands])

    known_sorted = sorted(known_mws, reverse=True)
    best = None
    for start in range(len(known_sorted) - k + 1):
        window = known_sorted[start : start + k]
        log_mws = np.log10(window)
        slope, intercept = np.polyfit(positions, log_mws, 1)
        predicted = slope * positions + intercept
        ss_res = float(np.sum((log_mws - predicted) ** 2))
        ss_tot = float(np.sum((log_mws - log_mws.mean()) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
        if best is None or r_squared > best[0]:
            best = (r_squared, window, float(slope), float(intercept))

    r_squared, window, slope, intercept = best

    if r_squared < min_r_squared:
        raise LadderCalibrationError(
            f"Best-effort ladder calibration using {k} band(s) had a poor fit "
            f"(best R²={r_squared:.2f} across all plausible size alignments) "
            "-- no assumed size assignment fits well enough to trust. Try "
            "--ladder-bands with an explicit, verified list, or --allow-heuristic."
        )

    return LadderCalibration(
        positions=positions,
        mws=np.array(window),
        slope=slope,
        intercept=intercept,
        r_squared=r_squared,
    )
