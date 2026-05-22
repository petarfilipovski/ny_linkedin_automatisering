"""Streamlit entrypoint for local dev and Streamlit Community Cloud."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PKG = _ROOT / "linkedin_automatisering"
_APP = _PKG / "app.py"

if not _APP.is_file():
    raise FileNotFoundError(
        f"Missing {_APP}. Ensure the linkedin_automatisering folder is committed "
        "(not as an empty git submodule)."
    )

sys.path.insert(0, str(_PKG))

import runpy

runpy.run_path(str(_APP), run_name="__main__")
