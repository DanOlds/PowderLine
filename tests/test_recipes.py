"""load_recipe_asset: encodings, document shape, error messages (review items 28, 11)."""
import json
import pytest

from powderline.recipes import load_recipe_asset

pytestmark = pytest.mark.core

RECIPE = {"schema_name": "gsasii.rietveld", "payload": {"note": "λ = 0.1665 Å"}}


def test_json_utf8_non_ascii_round_trips(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    assert load_recipe_asset(p) == RECIPE


def test_json_with_utf8_bom_loads(tmp_path):
    p = tmp_path / "r.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(RECIPE, ensure_ascii=False).encode("utf-8"))
    assert load_recipe_asset(p) == RECIPE


def test_yaml_mapping_loads_and_txt_is_yaml(tmp_path):
    p = tmp_path / "r.txt"
    p.write_text("schema_name: gsasii.rietveld\npayload: {}\n", encoding="utf-8")
    assert load_recipe_asset(p) == {"schema_name": "gsasii.rietveld", "payload": {}}


def test_empty_yaml_is_empty_dict(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("", encoding="utf-8")
    assert load_recipe_asset(p) == {}


def test_yaml_list_top_level_is_a_value_error_naming_the_file(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"r\.yaml.*mapping"):
        load_recipe_asset(p)


def test_malformed_json_is_a_value_error_naming_the_file(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"schema_name": "gsasii.rietveld",}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"r\.json.*not valid JSON"):
        load_recipe_asset(p)


def test_malformed_yaml_is_a_value_error_naming_the_file(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("schema_name: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"r\.yaml.*not valid YAML"):
        load_recipe_asset(p)


def test_unsupported_suffix(tmp_path):
    p = tmp_path / "r.toml"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported recipe format"):
        load_recipe_asset(p)


def test_client_normalize_recipe_uses_the_loader(tmp_path):
    from powderline.gateways.gsasii.client import GSASClient
    p = tmp_path / "r.json"
    p.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    assert GSASClient._normalize_recipe(p) == RECIPE
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        GSASClient._normalize_recipe(bad)
