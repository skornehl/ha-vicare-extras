"""Make custom_components/vicare_extras importable as `vicare_extras`."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
