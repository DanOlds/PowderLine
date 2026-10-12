"""Schema validation tests for easydiffraction.rietveld."""
import pytest
from pydantic import ValidationError

from powderline.gateways.easydiffraction.schema import (
    EasydiffractionRecipe,
    ENGINE_SCHEMA_VERSION,
)


def test_schema_name_required():
    """Schema name must be easydiffraction.rietveld."""
    with pytest.raises(ValidationError) as exc:
        EasydiffractionRecipe.model_validate({
            "core_schema_version": "1.0.0",
            "engine_schema_version": "1.0.0",
            "schema_name": "wrong.name",  # Wrong!
        })
    assert "schema_name" in str(exc.value)


def test_version_constants():
    """Version constants are defined."""
    assert ENGINE_SCHEMA_VERSION == "1.0.0"


def test_imports_no_easydiffraction():
    """Schema imports no easydiffraction (engine-free)."""
    import sys
    import powderline.gateways.easydiffraction.schema

    # Should be importable without easydiffraction installed
    assert "easydiffraction" not in sys.modules
