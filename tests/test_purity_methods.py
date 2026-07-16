import numpy as np
from skimage.io import imsave

from gel_extractor.purity.methods import METHOD_REGISTRY, MethodOutcome, run_all_methods, run_method


def _write_synthetic_gel(tmp_path, synthetic_gel):
    height = 300
    top_margin = int(height * 0.05)
    slope, intercept = -0.01, 2.3
    known_mws = [100.0, 50.0, 25.0]

    def post_crop_pos(mw):
        return (intercept - np.log10(mw)) / -slope

    ladder_bands = [(post_crop_pos(mw) + top_margin, 0.7) for mw in known_mws]
    target_pos = post_crop_pos(25.0) + top_margin

    image = synthetic_gel(height=height, band_specs=[ladder_bands, [(target_pos, 0.6)]])
    path = tmp_path / "gel.png"
    imsave(str(path), (image * 255).astype("uint8"))
    return path, known_mws


def test_registry_contains_rectangle_and_viterbi():
    assert "rectangle" in METHOD_REGISTRY
    assert "viterbi" in METHOD_REGISTRY
    assert METHOD_REGISTRY["rectangle"].maturity == "stable"
    assert METHOD_REGISTRY["viterbi"].maturity == "promising"
    # Every non-stable method must ship with a non-empty caveats list -- the
    # confidence-labeling requirement isn't just a tier string, see
    # purity.methods module docstring.
    for info in METHOD_REGISTRY.values():
        if info.maturity != "stable":
            assert info.caveats


def test_run_method_rectangle_succeeds(tmp_path, synthetic_gel):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)

    outcome = run_method("rectangle", str(path), target_mw=25.0, ladder_bands=known_mws)

    assert outcome.ok
    assert outcome.error is None
    assert outcome.method == "rectangle"
    assert outcome.maturity == "stable"
    assert outcome.results[0].confidence == "mw-matched"
    # Straight rectangle has no curve to draw.
    assert all(lane.centerline is None for lane in outcome.debug_info.lanes)


def test_run_method_viterbi_succeeds_and_populates_centerline(tmp_path, synthetic_gel):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)

    outcome = run_method("viterbi", str(path), target_mw=25.0, ladder_bands=known_mws)

    assert outcome.ok
    assert outcome.method == "viterbi"
    assert outcome.maturity == "promising"
    assert outcome.results[0].confidence == "mw-matched"
    # Every lane, including the ladder, should have a traced centerline --
    # this is the one thing that distinguishes viterbi from rectangle.
    assert all(lane.centerline is not None for lane in outcome.debug_info.lanes)


def test_run_method_unknown_key_raises():
    try:
        run_method("not-a-real-method", "unused.png", target_mw=25.0)
    except KeyError as exc:
        assert "not-a-real-method" in str(exc)
    else:
        raise AssertionError("expected KeyError for an unregistered method key")


def test_run_method_rescues_ladder_calibration_failure_into_outcome(tmp_path, synthetic_gel):
    # No --ladder/--ladder-bands and no --allow-heuristic -> LadderNotCalibratedError
    # inside the pipeline. Every adapter must catch this and return it as
    # MethodOutcome.error, never let it raise out of run_method.
    path, _ = _write_synthetic_gel(tmp_path, synthetic_gel)

    for key in ("rectangle", "viterbi"):
        outcome = run_method(key, str(path), target_mw=25.0)
        assert not outcome.ok
        assert outcome.error is not None
        assert outcome.results is None


def test_run_method_rescues_missing_file_into_outcome():
    for key in ("rectangle", "viterbi"):
        outcome = run_method(key, "/no/such/file.tif", target_mw=25.0, allow_heuristic=True)
        assert not outcome.ok
        assert "file.tif" in outcome.error or "No such file" in outcome.error


def test_run_all_methods_runs_every_registered_method(tmp_path, synthetic_gel):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)

    outcomes = run_all_methods(str(path), target_mw=25.0, ladder_bands=known_mws)

    assert [o.method for o in outcomes] == list(METHOD_REGISTRY.keys())
    assert all(isinstance(o, MethodOutcome) for o in outcomes)
    assert all(o.ok for o in outcomes)


def test_run_all_methods_one_failure_does_not_affect_others(tmp_path, synthetic_gel):
    # Bad --ladder-lane index: every method's own lane-count check should
    # independently catch this and report it, none should raise past the
    # adapter boundary or take any other method down with it.
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)

    outcomes = run_all_methods(str(path), target_mw=25.0, ladder_bands=known_mws, ladder_lane_index=99)

    assert len(outcomes) == len(METHOD_REGISTRY)
    assert all(not o.ok for o in outcomes)
