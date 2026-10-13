"""Tests for powderline.config_loader.ConfigLoader.

Covers the file-discovery paths (no config file → defaults, explicit missing
path → FileNotFoundError, valid YAML from a temporary file, tilde expansion,
empty YAML) and the MP API key resolution order (config value first, then the
MP_API_KEY environment variable, placeholder passthrough). Runs in the default
pixi environment and requires only pyyaml (already a PowderLine dependency).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import yaml

from powderline.config_loader import ConfigLoader

pytestmark = pytest.mark.core


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data))
    return path


# ---------------------------------------------------------------------------
# No config file — default values
# ---------------------------------------------------------------------------

class TestConfigLoaderDefaults:
    """When no config file is found, ConfigLoader should return sane defaults."""

    def test_loads_without_error_when_no_config(self, tmp_path, monkeypatch):
        """ConfigLoader() with no file at default locations should not raise."""
        # Ensure neither default location exists in tmp_path
        monkeypatch.chdir(tmp_path)
        loader = ConfigLoader()
        assert isinstance(loader.config, dict)

    def test_defaults_include_simulation_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        loader = ConfigLoader()
        defaults = loader.get_simulation_defaults()
        assert "wavelength" in defaults["instrument_defaults"]
        assert "broadening" in defaults["instrument_defaults"]
        assert "size_broadening" in defaults["phase_defaults"]
        assert "strain_broadening" in defaults["phase_defaults"]
        assert "background" in defaults

    def test_mp_api_key_returns_none_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MP_API_KEY", raising=False)
        loader = ConfigLoader()
        assert loader.get_mp_api_key() is None


# ---------------------------------------------------------------------------
# Explicit path
# ---------------------------------------------------------------------------

class TestConfigLoaderExplicitPath:

    def test_raises_file_not_found_for_nonexistent_path(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ConfigLoader(config_path=tmp_path / "does_not_exist.yaml")

    def test_loads_from_explicit_valid_path(self, tmp_path):
        cfg_file = _write_yaml(tmp_path / "config.yaml", {
            "materials_project": {"api_key": "test-api-key-xyz"},
            "simulation_defaults": {"instrument_defaults": {"wavelength": 1.54056}},
        })
        loader = ConfigLoader(config_path=cfg_file)
        assert loader.get_mp_api_key() == "test-api-key-xyz"

    def test_expands_tilde_in_path(self, tmp_path, monkeypatch):
        """Config paths with ~ are expanded before existence check."""
        # Point home to tmp_path so ~ resolves there. Path.expanduser() reads
        # HOME on POSIX and USERPROFILE on Windows, so set both to stay portable.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        cfg_file = _write_yaml(tmp_path / "my_config.yaml", {
            "materials_project": {"api_key": "home_key"},
        })
        loader = ConfigLoader(config_path=Path("~") / "my_config.yaml")
        assert loader.get_mp_api_key() == "home_key"


# ---------------------------------------------------------------------------
# Config content
# ---------------------------------------------------------------------------

class TestConfigLoaderContent:

    def test_get_mp_api_key_returns_configured_key(self, tmp_path):
        cfg_file = _write_yaml(tmp_path / "config.yaml", {
            "materials_project": {"api_key": "abc123"},
        })
        assert ConfigLoader(config_path=cfg_file).get_mp_api_key() == "abc123"

    def test_get_mp_api_key_returns_none_when_section_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MP_API_KEY", raising=False)
        cfg_file = _write_yaml(tmp_path / "config.yaml", {"other_section": {}})
        assert ConfigLoader(config_path=cfg_file).get_mp_api_key() is None

    def test_get_simulation_defaults_returns_file_values(self, tmp_path):
        """get_simulation_defaults returns the raw config block (layout
        normalization/merging happens in SimulationRecipeBuilder)."""
        cfg_file = _write_yaml(tmp_path / "config.yaml", {
            "simulation_defaults": {"instrument_defaults": {"wavelength": 0.1665}},
        })
        loader = ConfigLoader(config_path=cfg_file)
        defaults = loader.get_simulation_defaults()
        assert defaults["instrument_defaults"]["wavelength"] == pytest.approx(0.1665)

    def test_get_simulation_defaults_falls_back_to_defaults_if_absent(self, tmp_path):
        cfg_file = _write_yaml(tmp_path / "config.yaml", {
            "materials_project": {"api_key": "key"},
        })
        loader = ConfigLoader(config_path=cfg_file)
        defaults = loader.get_simulation_defaults()
        # Falls back to the built-in defaults — must contain at least wavelength
        assert "wavelength" in defaults["instrument_defaults"]

    def test_empty_yaml_file_uses_defaults(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MP_API_KEY", raising=False)
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("")
        loader = ConfigLoader(config_path=cfg_file)
        assert isinstance(loader.config, dict)
        assert loader.get_mp_api_key() is None


# ---------------------------------------------------------------------------
# MP_API_KEY environment variable fallback
# ---------------------------------------------------------------------------

class TestConfigLoaderEnvFallback:
    """get_mp_api_key falls back to the MP_API_KEY environment variable."""

    def test_env_var_used_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MP_API_KEY", "env-key-0123456789abcdef")
        loader = ConfigLoader()
        assert loader.get_mp_api_key() == "env-key-0123456789abcdef"

    def test_env_var_used_when_config_key_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MP_API_KEY", "env-key-0123456789abcdef")
        cfg_file = _write_yaml(tmp_path / "config.yaml", {"other_section": {}})
        loader = ConfigLoader(config_path=cfg_file)
        assert loader.get_mp_api_key() == "env-key-0123456789abcdef"

    def test_env_var_used_when_config_key_is_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MP_API_KEY", "env-key-0123456789abcdef")
        cfg_file = _write_yaml(tmp_path / "config.yaml", {
            "materials_project": {"api_key": "YOUR_API_KEY_HERE"},
        })
        loader = ConfigLoader(config_path=cfg_file)
        assert loader.get_mp_api_key() == "env-key-0123456789abcdef"

    def test_config_key_takes_precedence_over_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MP_API_KEY", "env-key-0123456789abcdef")
        cfg_file = _write_yaml(tmp_path / "config.yaml", {
            "materials_project": {"api_key": "config-key"},
        })
        loader = ConfigLoader(config_path=cfg_file)
        assert loader.get_mp_api_key() == "config-key"

    def test_placeholder_passed_through_when_env_unset(self, tmp_path, monkeypatch):
        """CLI relies on seeing the placeholder to print setup instructions."""
        monkeypatch.delenv("MP_API_KEY", raising=False)
        cfg_file = _write_yaml(tmp_path / "config.yaml", {
            "materials_project": {"api_key": "YOUR_API_KEY_HERE"},
        })
        loader = ConfigLoader(config_path=cfg_file)
        assert loader.get_mp_api_key() == "YOUR_API_KEY_HERE"
