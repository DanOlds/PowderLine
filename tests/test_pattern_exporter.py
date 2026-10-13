"""Smoke tests for powderline.gateways.gsasii.pattern_exporter.PatternExporter.

The module is pure pandas/numpy (no GSAS-II), so it is fully exercisable in
isolation. export_simulation_results consumes the in-band fit_profile dict
(result['fit_profile']); the file-based extract_calculated_pattern utility is
exercised against a synthesized fit_profile.txt.
"""
import numpy as np
import pytest

from powderline.gateways.gsasii.pattern_exporter import PatternExporter


def _write_fit_profile(path):
    """Write a minimal tab-separated fit_profile.txt with the required columns."""
    lines = ["two_theta\ty_obs\ty_calc\tresidual\n"]
    for i in range(5):
        tth = 10.0 + i
        lines.append(f"{tth:.4f}\t{100 + i}\t{90 + i}\t{10}\n")
    path.write_text("".join(lines), encoding="utf-8")


def test_extract_calculated_pattern(tmp_path):
    fp = tmp_path / "fit_profile.txt"
    _write_fit_profile(fp)
    two_theta, y_calc = PatternExporter.extract_calculated_pattern(fp)
    assert len(two_theta) == 5
    assert len(y_calc) == 5
    assert two_theta[0] == pytest.approx(10.0)
    assert y_calc[0] == pytest.approx(90.0)


def test_extract_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        PatternExporter.extract_calculated_pattern(tmp_path / "does_not_exist.txt")


def test_export_to_chi_writes_two_columns(tmp_path):
    two_theta = np.array([10.0, 11.0, 12.0])
    intensity = np.array([1.0, 2.0, 3.0])
    out = tmp_path / "pattern.chi"
    PatternExporter.export_to_chi(two_theta, intensity, out)
    assert out.exists()
    data_lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    assert len(data_lines) == 3
    first = data_lines[0].split()
    assert float(first[0]) == pytest.approx(10.0)
    assert float(first[1]) == pytest.approx(1.0)


def test_export_to_chi_with_metadata_header(tmp_path):
    out = tmp_path / "meta.chi"
    PatternExporter.export_to_chi(
        np.array([10.0, 20.0]),
        np.array([5.0, 6.0]),
        out,
        metadata={"formula": "LaB6", "material_id": "mp-1", "wavelength": 0.5},
    )
    text = out.read_text(encoding="utf-8")
    assert "# Material: LaB6" in text
    assert "# Material ID: mp-1" in text


def test_export_to_chi_length_mismatch_raises(tmp_path):
    with pytest.raises(ValueError, match="length mismatch"):
        PatternExporter.export_to_chi(
            np.array([1.0, 2.0]), np.array([1.0]), tmp_path / "x.chi"
        )


def test_export_to_chi_empty_raises(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        PatternExporter.export_to_chi(
            np.array([]), np.array([]), tmp_path / "x.chi"
        )


def test_export_to_chi_nan_raises(tmp_path):
    with pytest.raises(ValueError, match="NaN"):
        PatternExporter.export_to_chi(
            np.array([1.0, np.nan]), np.array([1.0, 2.0]), tmp_path / "x.chi"
        )


def _fit_profile_data(n=5):
    """In-band fit_profile dict as returned under result['fit_profile']."""
    return {
        "two_theta": [10.0 + i for i in range(n)],
        "y_obs": [100.0 + i for i in range(n)],
        "y_calc": [90.0 + i for i in range(n)],
        "residual": [10.0] * n,
    }


def test_extract_calculated_pattern_from_data():
    two_theta, y_calc = PatternExporter.extract_calculated_pattern_from_data(
        _fit_profile_data())
    assert len(two_theta) == 5
    assert two_theta[0] == pytest.approx(10.0)
    assert y_calc[0] == pytest.approx(90.0)


def test_extract_from_empty_data_raises():
    with pytest.raises(ValueError, match="empty"):
        PatternExporter.extract_calculated_pattern_from_data({})


def test_extract_from_data_missing_column_raises():
    with pytest.raises(ValueError, match="y_calc"):
        PatternExporter.extract_calculated_pattern_from_data(
            {"two_theta": [1.0, 2.0]})


def test_export_simulation_results_end_to_end(tmp_path):
    """Export works from the in-band data alone — no fit_profile.txt on disk."""
    chi_path = PatternExporter.export_simulation_results(
        output_dir=tmp_path,
        formula="LaB6",
        material_id="mp-2680",
        wavelength=0.4592,
        fit_profile_data=_fit_profile_data(),
    )
    assert chi_path.exists()
    assert chi_path.name == "mp-2680_LaB6_simulated.chi"
    text = chi_path.read_text(encoding="utf-8")
    assert "# Material: LaB6" in text
    data_lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert len(data_lines) == 5
