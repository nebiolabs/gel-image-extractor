"""Integration tests against real example gel images in data/.

data/ is gitignored (not committed), so these are skipped if it's not
present in the current checkout -- see AGENTS.md "Data Inventory".
"""

from pathlib import Path

import pytest

from gel_extractor.purity.analysis import analyze_image

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
# NOTE: the equivalent file under data/daria_data/attachments/ is a Benchling
# attachment-viewer *screenshot* (UI chrome included), not a clean gel photo
# -- confirmed by the filename visible inside that screenshot matching this
# file. Use this clean version for real image processing; see AGENTS.md.
HPYCH4IV_IMAGE = DATA_DIR / "decodeon_gel_images" / "Protein Purity" / "8.6.25 Protein Purity.tif"

pytestmark = pytest.mark.skipif(
    not HPYCH4IV_IMAGE.exists(),
    reason="local example data (data/) not present in this checkout",
)


def test_purity_runs_on_real_gel_image():
    # No verified ladder band sizes exist yet (see QUESTIONS_FOR_USERS.md),
    # so this uses --allow-heuristic rather than MW-matching.
    results, ladder_lane_index, _debug_info = analyze_image(
        str(HPYCH4IV_IMAGE),
        target_mw=29.267,  # HpyCH4IV MW per the submitter's email: 29,267 Da
        allow_heuristic=True,
    )
    assert len(results) > 0
    for r in results:
        assert r.purity_percent is None or 0 <= r.purity_percent <= 100


def test_dilution_series_purity_is_self_consistent():
    """Diluting a sample shouldn't change its purity, only its total signal.

    This is the project's primary correctness signal in the absence of
    external ground truth -- see AGENTS.md Design Decisions and
    QUESTIONS_FOR_USERS.md.
    """
    results, _, _debug_info = analyze_image(str(HPYCH4IV_IMAGE), target_mw=29.267, allow_heuristic=True)
    purities = [r.purity_percent for r in results if r.purity_percent is not None]
    assert len(purities) >= 3, f"expected several dilution lanes with results, got {purities}"

    spread = max(purities) - min(purities)
    # Deliberately loose bound: this runs in --allow-heuristic (largest-band)
    # mode since no verified ladder band sizes exist yet (QUESTIONS_FOR_USERS.md),
    # and the heuristic shows a real, understood bias -- fainter dilution
    # lanes lose their faint contaminant bands below the detection threshold
    # first, which inflates apparent purity as dilution increases. Observed
    # spread as of 2026-07-14 was ~33 points (a lane with zero detected bands
    # used to report a fabricated 0% instead of "not-found", inflating the
    # spread to 82 -- see AGENTS.md Implementation Status); this bound exists
    # to catch a much worse future regression, not to assert current tuning
    # is good. Expected to tighten substantially once MW-matching can be
    # validated against a verified ladder.
    assert spread < 70, f"purity %% spread across dilution lanes was {spread:.1f} points ({purities})"


def test_largest_band_selection_excludes_corroborated_crop_artifact():
    """Regression test for a confirmed real bug (AGENTS.md Known
    Limitations, 2026-07-22): a broad, roughly uniform-intensity leftover
    glued to the crop boundary, present at a consistent position across
    most sample lanes of this exact image, used to win
    band_selection="largest" on area alone in several lanes -- reporting an
    obviously-wrong ~137-218 kDa instead of the real ~29 kDa target. Lanes 4
    and 5 were the clearest confirmed cases (49%/136.9 kDa and 73%/139.0 kDa
    before the fix). Deliberately a real-image test, not synthetic --
    reproducing this exact baseline-correction/peak-detection interaction
    with `make_synthetic_gel`'s simple gaussian bands proved unreliable (a
    smooth wide artifact gets absorbed as baseline; a jagged one produces
    nested duplicate peak detections a real photographed gel doesn't).
    """
    results, _, _ = analyze_image(
        str(HPYCH4IV_IMAGE), target_mw=None, ladder="P7719", allow_heuristic=True, band_selection="largest"
    )
    by_lane = {r.lane: r for r in results}
    for lane in (4, 5):
        mw = by_lane[lane].matched_band_mw
        assert mw is not None and mw < 100, f"lane {lane} regressed to an implausible MW: {mw}"
