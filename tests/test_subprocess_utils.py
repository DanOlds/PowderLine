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
