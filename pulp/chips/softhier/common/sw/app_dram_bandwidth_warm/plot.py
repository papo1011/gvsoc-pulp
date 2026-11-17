#!/usr/bin/env python3
"""Plot the warm size sweep with the shared bandwidth plotting code."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common.plot_bandwidth import main

if __name__ == "__main__":
    main("warm", Path(__file__).parent / "assets")
