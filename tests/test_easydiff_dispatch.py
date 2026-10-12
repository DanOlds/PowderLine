"""Dispatcher wiring for engine="easydiffraction" (no easydiffraction needed)."""
import pytest

from powderline.engine import _ENGINES, run


def test_easydiffraction_in_engines_tuple():
    assert "easydiffraction" in _ENGINES


def test_unknown_engine_message_lists_easydiffraction(tmp_path):
    with pytest.raises(ValueError, match="easydiffraction"):
        run({}, tmp_path, engine="bogus")


def test_dispatch_reaches_easydiff_engine(tmp_path, monkeypatch):
    import powderline.gateways.easydiffraction.engine as ede
    calls = {}

    def fake(recipe, output_dir, *, verbose=False, validate_only=False):
        calls["recipe"] = recipe
        calls["validate_only"] = validate_only
        return {"success": True}

    monkeypatch.setattr(ede, "run_easydiffraction_recipe", fake)
    result = run({"schema_name": "GSASII_Rietveld"}, tmp_path,
                 engine="easydiffraction", validate_only=True)
    assert result == {"success": True}
    assert calls["validate_only"] is True
