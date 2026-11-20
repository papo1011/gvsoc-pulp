#!/usr/bin/env python3
"""Attribute DRAMSys transactions to each read sample."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_pipelined.summarize import FIELDS, measurements
from dram_common.analysis import query_main

if __name__ == "__main__":
    query_main(FIELDS, measurements)
