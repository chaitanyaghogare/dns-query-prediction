"""Live demo entry point (no install needed):  python demo.py run   |   python demo.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dnscache.demo.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
