"""Entry script for the packaged .exe (PyInstaller needs a plain script)."""

import sys

from ds5forge.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
