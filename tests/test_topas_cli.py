"""CLI smoke tests + committed-artifact/golden consistency (plan §7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powderline.gateways.topas import kicker
from powderline.gateways.topas import runner as tc_runner

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "tests" / "data" / "legacy_026"  # 0.26.0 snapshots of the examples (re/07, P-16)
LEGACY_TOPAS_OUT = REPO / "examples"  # the TOPAS 7.25 goldens under examples/*/output/topas/ (go with P-02)
GOLDEN = REPO / "tests" / "data" / "topas"
CASES = ["example_LaB6", "example_DRX_33"]


# --- --validate-only --------------------------------------------------------


@pytest.mark.parametrize("name", CASES)
def test_validate_only_ok(name, capsys):
    rc = kicker.main([str(EXAMPLES / name / "input.json"), "--validate-only"])
    assert rc == 0
    assert "OK:" in capsys.readouterr().out


def test_validate_only_reports_invalid(tmp_path, capsys):
    recipe = json.loads((EXAMPLES / "example_LaB6" / "input.json").read_text())
    # rhombohedral :R setting is genuinely untranslatable (not a permissive case)
    recipe["payload"]["phases"]["LaB6"]["structure"]["space_group"] = "R -3 m :R"
    bad = tmp_path / "input.json"
    bad.write_text(json.dumps(recipe))
    rc = kicker.main([str(bad), "--validate-only"])
    assert rc == 1
    assert "INVALID:" in capsys.readouterr().err


def test_missing_recipe_returns_1(tmp_path, capsys):
    rc = kicker.main([str(tmp_path / "nope.json")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


# --- full run ---------------------------------------------------------------


@pytest.mark.parametrize("name", CASES)
def test_full_run_writes_both_files(name, tmp_path):
    out = tmp_path / "topas"
    rc = kicker.main([str(EXAMPLES / name / "input.json"), "--output", str(out)])
    assert rc == 0
    inp = out / f"{name}.inp"
    xye = out / f"{name}.xye"
    assert inp.exists() and xye.exists()
    # byte-identical to the committed golden
    assert inp.read_text() == (GOLDEN / f"{name}.inp").read_text()
    assert xye.read_text() == (GOLDEN / f"{name}.xye").read_text()


def test_default_output_path(tmp_path):
    # a recipe named input.json in its own dir writes to <parent>/output/topas
    src = json.loads((EXAMPLES / "example_LaB6" / "input.json").read_text())
    case_dir = tmp_path / "example_case"
    case_dir.mkdir()
    (case_dir / "input.json").write_text(json.dumps(src))
    rc = kicker.main([str(case_dir / "input.json")])
    assert rc == 0
    assert (case_dir / "output" / "topas" / "example_case.inp").exists()


# --- committed artifacts vs goldens -----------------------------------------


# --- end-to-end generate -> run -> parse (mocked TOPAS) ---------------------


@pytest.mark.parametrize("name", CASES)
def test_end_to_end_with_mocked_topas(name, tmp_path, monkeypatch):
    out = tmp_path / "topas"
    fixture = (GOLDEN / f"{name}_results.csv").read_text()

    def fake_run(inp_path, topas_dir=None, topas_version=None):
        # simulate TOPAS writing the results file next to the generated INP
        Path(inp_path).with_name(f"{name}_results.csv").write_text(fixture)
        return tc_runner.RunResult("ran", 0, Path("/fake/tc.exe"), ["tc"])

    monkeypatch.setattr(kicker.tc_runner, "run_topas", fake_run)
    rc = kicker.main([str(EXAMPLES / name / "input.json"), "--output", str(out)])
    assert rc == 0
    refined = out / "refined_parameters.csv"
    assert refined.exists()
    committed = LEGACY_TOPAS_OUT / name / "output" / "topas" / "refined_parameters.csv"
    assert refined.read_text() == committed.read_text()


def test_end_to_end_profile_and_peaks(tmp_path, monkeypatch):
    name = "example_LaB6"
    out = tmp_path / "topas"
    results = (GOLDEN / f"{name}_results.csv").read_text()
    profile = (GOLDEN / f"{name}_topas_profile.txt").read_text()
    peaks = (GOLDEN / "LaB6_topas_peaks.txt").read_text()

    def fake_run(inp_path, topas_dir=None, topas_version=None):
        d = Path(inp_path).parent
        (d / f"{name}_results.csv").write_text(results)
        (d / f"{name}_topas_profile.txt").write_text(profile)
        (d / "LaB6_topas_peaks.txt").write_text(peaks)
        return tc_runner.RunResult("ran", 0, Path("/fake/tc.exe"), ["tc"])

    monkeypatch.setattr(kicker.tc_runner, "run_topas", fake_run)
    rc = kicker.main([str(EXAMPLES / name / "input.json"), "--output", str(out)])
    assert rc == 0
    fit_profile = out / "fit_profile.txt"
    peak_list = out / "LaB6_peak_list_report.csv"
    assert fit_profile.exists() and peak_list.exists()
    assert fit_profile.read_text().splitlines()[0].startswith("two_theta\ty_obs")
    assert peak_list.read_text().splitlines()[0].startswith("h,k,l,multiplicity")


def test_empty_results_fails_loudly(tmp_path, monkeypatch, capsys):
    name = "example_LaB6"

    def fake_run(inp_path, topas_dir=None, topas_version=None):
        # simulate a run that produced an empty/garbage results file
        Path(inp_path).with_name(f"{name}_results.csv").write_text("parameter,value,esd\n")
        return tc_runner.RunResult("ran", 0, Path("/fake/tc.exe"), ["tc"])

    monkeypatch.setattr(kicker.tc_runner, "run_topas", fake_run)
    rc = kicker.main([str(EXAMPLES / name / "input.json"), "--output", str(tmp_path / "o")])
    assert rc == 1
    assert "no parseable parameters" in capsys.readouterr().err


def test_no_run_generates_only(tmp_path):
    out = tmp_path / "topas"
    rc = kicker.main([str(EXAMPLES / "example_LaB6" / "input.json"), "--output", str(out), "--no-run"])
    assert rc == 0
    assert (out / "example_LaB6.inp").exists()
    assert not (out / "refined_parameters.csv").exists()


def test_missing_tc_notes_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        kicker.tc_runner, "run_topas", lambda *a, **k: tc_runner.RunResult("tc_not_found", None, None, [])
    )
    out = tmp_path / "topas"
    rc = kicker.main([str(EXAMPLES / "example_LaB6" / "input.json"), "--output", str(out)])
    assert rc == 0
    assert "not found" in capsys.readouterr().out
    assert (out / "example_LaB6.inp").exists()


def test_run_flag_errors_without_tc(tmp_path, monkeypatch):
    monkeypatch.setattr(
        kicker.tc_runner, "run_topas", lambda *a, **k: tc_runner.RunResult("tc_not_found", None, None, [])
    )
    rc = kicker.main([str(EXAMPLES / "example_LaB6" / "input.json"), "--output", str(tmp_path), "--run"])
    assert rc == 1


@pytest.mark.parametrize("name", CASES)
def test_parse_results_writes_roundtrip_tables(name, tmp_path):
    out = tmp_path / "topas"
    rc = kicker.main([
        str(EXAMPLES / name / "input.json"),
        "--parse-results", str(GOLDEN / f"{name}_results.csv"),
        "--output", str(out),
    ])
    assert rc == 0
    refined = out / "refined_parameters.csv"
    assert refined.exists()
    # fit_summary.csv is intentionally NOT emitted (GSAS-II parity: fit stats
    # live in the run() dict, not a bespoke file).
    assert not (out / "fit_summary.csv").exists()
    # byte-identical to the committed round-trip artifact
    committed = LEGACY_TOPAS_OUT / name / "output" / "topas" / "refined_parameters.csv"
    assert refined.read_text() == committed.read_text()


# NOTE: generated .inp/.xye are no longer committed under examples/*/output/topas/
# (they are regenerable via topas-kicker); the canonical byte-equality baselines
# are the goldens in tests/data/topas/, exercised by the tests above.
