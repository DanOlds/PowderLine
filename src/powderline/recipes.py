"""Recipe files: load a recipe asset from disk (engine-free).

``load_recipe_asset`` moved here from ``powderline.kicker`` (re/07, P-06) so
the public API can read a recipe without importing any engine. Interpreting
the recipe is the gateways' job (:func:`powderline.validate`).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_recipe_asset(recipe_path: Path) -> dict:
    """Read a recipe file and return it as a dict, uninterpreted.

    ``.json`` is parsed as JSON; ``.yaml`` / ``.yml`` / ``.txt`` as YAML (an
    empty YAML document gives ``{}``). Files are read as UTF-8 (a leading BOM
    is accepted). The content is not validated: pass it to
    :func:`powderline.validate` or :func:`powderline.run`.

    Raises:
        ValueError: the suffix is none of the above (``Unsupported recipe format``);
            the text is not valid JSON / YAML; or the document is not a mapping.
            The message starts with the file path.
        OSError: the file cannot be read.
    """
    recipe_path = Path(recipe_path)
    ext = recipe_path.suffix.lower()
    if ext not in {".json", ".yaml", ".yml", ".txt"}:
        raise ValueError(f"Unsupported recipe format: {recipe_path}")
    text = recipe_path.read_text(encoding="utf-8-sig")
    if ext == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{recipe_path}: not valid JSON ({exc})") from exc
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"{recipe_path}: not valid YAML ({exc})") from exc
        if data is None:
            data = {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{recipe_path}: a recipe must be a mapping with schema_name and payload, "
            f"got {type(data).__name__}")
    return data
