#!/usr/bin/env python3
"""Query DRAMSys records for this benchmark's measurement windows."""

import sys
from pathlib import Path

# Keep these scripts runnable directly from the SDK without installing a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_multi.summarize import FIELDS, measurements
from dram_common.analysis import query_main, query_measurements


def query(log, directory):
    return query_measurements(FIELDS, measurements(log), directory)


if __name__ == "__main__":
    query_main(FIELDS, measurements)
