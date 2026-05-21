"""Run Streamlit from repo root: `streamlit run app.py`"""
import runpy
import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parent / "linkedin_automatisering"
sys.path.insert(0, str(_pkg))
runpy.run_path(str(_pkg / "app.py"), run_name="__main__")
