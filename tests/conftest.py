"""Test setup.

Tests import the integration as `custom_components.vicare_extras`. Files
named `test_*_hass.py` need the Home Assistant test harness
(pytest-homeassistant-custom-component, installed in CI); without it they are
skipped so the pure logic tests also run against a plain Home Assistant.
"""

from __future__ import annotations

import importlib.util

import pytest

HAS_HARNESS = importlib.util.find_spec("pytest_homeassistant_custom_component") is not None

collect_ignore_glob = [] if HAS_HARNESS else ["test_*_hass.py"]

if HAS_HARNESS:

    @pytest.fixture
    def extras_hass(hass, enable_custom_integrations):
        """A hass instance that loads integrations from custom_components."""
        return hass
