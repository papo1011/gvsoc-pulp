#!/usr/bin/env python3
"""Count, for each embeddings sample, the DRAM reads, row openings and banks it used."""

import argparse
import csv
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_embeddings.summarize import FIELDS, TABLE, TABLE_BYTES, measurements
from dram_common.analysis import ROW_QUERY

COLUMNS = (
    "transactions",
    "dram_bytes",
    "activates",
    "activates_per_token",
    "bursts_per_activate",
    "bus_window_ns",
    "bus_GB_per_s",
    "banks",
    "pc_bank_groups",
)


def query(rows, directory):
    paths = list(directory.glob("DRAMSysRecordable0_*.tdb"))
    if len(paths) != 1:
        raise ValueError(f"Expected one database for channel 0, found {len(paths)}")
    with closing(sqlite3.connect(paths[0].resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("SELECT UnitOfTime FROM GeneralInfo").fetchone() != ("PS",):
            raise ValueError("Expected picosecond timestamps")
        for row in rows:
            args = (row["start_ps"], row["end_ps"], TABLE, TABLE + TABLE_BYTES)
            count, data, acts, begin, end, banks, groups, writes = db.execute(
                ROW_QUERY, args
            ).fetchone()
            key = row["piece"], row["order"], row["sample"]
            if writes or not count:
                raise ValueError(f"Expected only reads in sample {key}")
            if data < row["bytes"]:
                raise ValueError(f"DRAM moved fewer bytes than requested: {key}")
            yield (
                *(row[field] for field in FIELDS),
                count,
                data,
                acts,
                f"{acts / row['tokens']:.2f}",
                f"{count / max(acts, 1):.2f}",
                f"{(end - begin) / 1000:.3f}",
                f"{data * 1000 / (end - begin):.6f}",
                banks,
                groups,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("database_dir", type=Path)
    args = parser.parse_args()
    try:
        rows = list(query(measurements(args.log.read_text()), args.database_dir))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.writer(sys.stdout)
    writer.writerow((*FIELDS, *COLUMNS))
    writer.writerows(rows)


if __name__ == "__main__":
    main()
