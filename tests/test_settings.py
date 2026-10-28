"""Tests for the settings."""

import re
from pathlib import Path

from swissgeo_oar.settings import Settings

_PYGEOAPI_CONFIG = Path(__file__).parent.parent / "pygeoapi-config.yml"


def test_pygeoapi_config_api_prefix_default_matches_settings() -> None:
    # pygeoapi reads server.url from its configuration file itself, so the fallback
    # value there has to be kept in sync with the settings default.
    match = re.search(r"\$\{API_PREFIX:-([^}]*)\}", _PYGEOAPI_CONFIG.read_text())
    assert match, f"${{API_PREFIX:-...}} not found in {_PYGEOAPI_CONFIG.name}"
    assert match.group(1) == Settings.model_fields["api_prefix"].default
