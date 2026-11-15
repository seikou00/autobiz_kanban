"""CLI entry point for delivery report render."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from hooks.delivery_report.cli import main

if __name__ == "__main__":
    raise SystemExit(main("render"))
