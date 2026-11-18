#!/usr/bin/env python3
"""Query the full dataset address range using the shared DRAMSys analysis."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_tiled.summarize import FIELDS, measurements
from dram_common.analysis import query_main

if __name__ == "__main__":
    query_main(FIELDS, measurements)
