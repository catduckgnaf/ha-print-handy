"""Test bootstrap.

Lets the HA-free modules (model, api, const) be tested without Home
Assistant installed: the component package is registered by path with
its real __init__ left unexecuted, and the single homeassistant name
const.py needs (Platform) is stubbed. In CI, requirements-test.txt
installs the real test harness for the HA-level tests.
"""

import enum
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT_DIR = ROOT / "custom_components" / "print_handy"


def _ensure_package(name: str, path: Path) -> None:
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module


if importlib.util.find_spec("homeassistant") is None:
    # No Home Assistant installed (plain local run): register the package
    # by path and stub the one HA name const.py needs. With HA installed
    # (CI), leave all loading to the real test harness.
    _ensure_package("custom_components", ROOT / "custom_components")
    _ensure_package("custom_components.print_handy", COMPONENT_DIR)
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    const = types.ModuleType("homeassistant.const")

    class Platform(enum.Enum):
        """Stand-in for homeassistant.const.Platform (only what const.py uses)."""

        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        NUMBER = "number"
        SENSOR = "sensor"

    const.Platform = Platform
    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.const"] = const
    ha.const = const
