#!/usr/bin/python3
"""Root-owned entry point for controlled pre-commit numbering abort recovery."""
from pathlib import Path
import sys

LIBRARY = Path("/usr/local/lib/inventory-numbering")
if str(LIBRARY) not in sys.path:
    sys.path.insert(0, str(LIBRARY))

from numbering_abort import production_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(production_main())
