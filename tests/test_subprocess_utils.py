"""subprocess_utils: command construction is cwd-independent (review item 17)."""
from pathlib import Path

import pytest

import subprocess_utils

pytestmark = pytest.mark.core


def test_kicker_command_without_repo_root_is_unchanged(monkeypatch):
    monkeypatch.setenv(subprocess_utils.PIXI_ENV_OVERRIDE_VAR, "gsasii")
    assert subprocess_utils.kicker_command("x.json") == ["pixi", "run", "-e", "gsasii", "kicker", "--no-server", "x.json"]


def test_kicker_command_with_repo_root_pins_the_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv(subprocess_utils.PIXI_ENV_OVERRIDE_VAR, "gsasii")
    cmd = subprocess_utils.kicker_command("x.json", repo_root=tmp_path)
    assert cmd[:4] == ["pixi", "run", "--manifest-path", str(tmp_path / "pixi.toml")]
    assert cmd[4:] == ["-e", "gsasii", "kicker", "--no-server", "x.json"]


def test_conftest_example_paths_are_absolute():
    import conftest
    assert conftest.EXAMPLES.is_absolute() and (conftest.EXAMPLES / "example_LaB6" / "input.json").is_file()


def test_no_cwd_relative_example_paths_in_tests():
    """Every test module resolves examples/ from its own location, never from the cwd (review item 17)."""
    tests_dir = Path(__file__).resolve().parent
    offenders = [f"{p.name}:{n}" for p in sorted(tests_dir.glob("*.py"))
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if 'Path("' + 'examples/' in line]  # built so this scanner does not match itself
    assert offenders == []


def test_conftest_reads_text_with_an_explicit_encoding():
    """Review item 28: no platform-default text reads in the shared fixtures."""
    conftest = (Path(__file__).resolve().parent / "conftest.py").read_text(encoding="utf-8")
    offenders = [n for n, line in enumerate(conftest.splitlines(), 1)
                 if ".read_text()" in line or "open(" in line and "encoding" not in line and "os.open" not in line]
    assert offenders == []
