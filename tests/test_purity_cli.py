import json

import numpy as np
from skimage.io import imsave

from gel_extractor.cli import main


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


def test_cli_analyze_prints_table(tmp_path, synthetic_gel, capsys):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)
    ladder_bands_arg = ",".join(str(v) for v in known_mws)

    exit_code = main(
        ["purity", "analyze", str(path), "--target-mw", "25", "--ladder-bands", ladder_bands_arg]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Ladder detected in lane 1" in captured.out
    assert "mw-matched" in captured.out


def test_cli_analyze_json_to_stdout(tmp_path, synthetic_gel, capsys):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)
    ladder_bands_arg = ",".join(str(v) for v in known_mws)

    exit_code = main(
        ["purity", "analyze", str(path), "--target-mw", "25", "--ladder-bands", ladder_bands_arg, "--json"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ladder_lane"] == 1
    assert len(payload["results"]) == 1
    assert payload["results"][0]["confidence"] == "mw-matched"


def test_cli_analyze_csv_to_file(tmp_path, synthetic_gel, capsys):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)
    ladder_bands_arg = ",".join(str(v) for v in known_mws)
    csv_path = tmp_path / "out.csv"

    exit_code = main(
        [
            "purity",
            "analyze",
            str(path),
            "--target-mw",
            "25",
            "--ladder-bands",
            ladder_bands_arg,
            "--csv",
            str(csv_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Ladder detected in lane 1" in captured.out  # table still printed alongside file output
    assert csv_path.exists()
    content = csv_path.read_text()
    assert "purity_percent" in content
    assert "mw-matched" in content


def test_cli_analyze_debug_image_to_file(tmp_path, synthetic_gel, capsys):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)
    ladder_bands_arg = ",".join(str(v) for v in known_mws)
    debug_path = tmp_path / "out_debug.png"

    exit_code = main(
        [
            "purity",
            "analyze",
            str(path),
            "--target-mw",
            "25",
            "--ladder-bands",
            ladder_bands_arg,
            "--debug",
            str(debug_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert debug_path.exists()
    assert "Debug image written to" in captured.out


def test_cli_analyze_debug_image_default_path(tmp_path, synthetic_gel, capsys):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)
    ladder_bands_arg = ",".join(str(v) for v in known_mws)

    exit_code = main(
        ["purity", "analyze", str(path), "--target-mw", "25", "--ladder-bands", ladder_bands_arg, "--debug"]
    )

    assert exit_code == 0
    assert (tmp_path / "gel_debug.png").exists()


def test_cli_analyze_errors_without_ladder_info(tmp_path, synthetic_gel, capsys):
    path, _ = _write_synthetic_gel(tmp_path, synthetic_gel)

    exit_code = main(["purity", "analyze", str(path), "--target-mw", "25"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error:" in captured.err


def test_cli_analyze_errors_cleanly_on_missing_image(tmp_path, capsys):
    # A nonexistent/unreadable image file must print a clean one-line error,
    # not a raw traceback -- see AGENTS.md/README's MVP-polish notes (2026-07-14).
    missing_path = tmp_path / "does_not_exist.tif"

    exit_code = main(["purity", "analyze", str(missing_path), "--target-mw", "25", "--allow-heuristic"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_analyze_errors_cleanly_on_unwritable_debug_path(tmp_path, synthetic_gel, capsys):
    path, known_mws = _write_synthetic_gel(tmp_path, synthetic_gel)
    ladder_bands_arg = ",".join(str(v) for v in known_mws)
    bad_debug_path = tmp_path / "no_such_subdir" / "out.png"

    exit_code = main(
        [
            "purity", "analyze", str(path),
            "--target-mw", "25", "--ladder-bands", ladder_bands_arg,
            "--debug", str(bad_debug_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error:" in captured.err
    assert "Traceback" not in captured.err
