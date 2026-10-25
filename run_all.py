"""One command to reproduce everything (works without `pip install -e .`):

    python run_all.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dnscache.run import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
