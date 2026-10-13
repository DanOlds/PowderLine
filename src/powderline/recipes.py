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
    empty YAML document gives ``{}``). The content is not validated: pass it to
    :func:`powderline.validate` or :func:`powderline.run`.

    Raises:
        ValueError: the suffix is none of the above (``Unsupported recipe format``).
        OSError: the file cannot be read.
    """
    recipe_path = Path(recipe_path)
    ext = recipe_path.suffix.lower()
    text = recipe_path.read_text()
    if ext == ".json":
        return json.loads(text)
    if ext in {".yaml", ".yml", ".txt"}:
        return yaml.safe_load(text) or {}
    raise ValueError(f"Unsupported recipe format: {recipe_path}")
