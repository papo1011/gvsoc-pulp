#!/usr/bin/env python3
"""Extract the DRAM request trace of chosen samples into a small CSV.

One row per 32 B read request: when it reached the controller, when its row
was opened (if it was), when its read command was issued, when its burst was
on the data bus, and on which pseudo-channel, bank group (0-7 within the
pseudo-channel), bank (0-3 within the group) and row. The
figures in plot.py are drawn from this CSV, so the multi-GB database does
not need to be kept in the repository.

With --spacing it instead counts, for every sample of the sweep, the spacing
between consecutive bursts on each pseudo-channel: whether the two bursts were
in the same bank group (tCCD_L applies) or not (tCCD_S), the start-to-start
gap, and the two bank groups.

A size can carry an address window, SIZE:FIRST:END, to keep only the requests
FIRST to END bytes into the sample: a zoom into a large block.

Examples (the assets of this app):
  query_trace.py simulation.log RUN_DIR --size 512 --size 1048576:0x3ff800:0x400800 \
      > trace.csv
  query_trace.py simulation.log RUN_DIR --spacing > spacing.csv
"""

import argparse
import csv
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_pipelined.summarize import measurements

COLUMNS = (
    "bytes",
    "sample",
    "request",
    "address",
    "pseudo_channel",
    "bank_group",
    "bank",
    "row",
    "column",
    "generated_ps",
    "activate_ps",
    "read_command_ps",
    "burst_begin_ps",
    "burst_end_ps",
    "response_ps",
)

# DRAMSys numbers bank groups and banks across the whole channel; the CSV keeps
# them local, as in the HBM4 emulation memspec: 8 bank groups of 4 banks each
# per pseudo-channel.
BANK_GROUPS, BANKS_PER_GROUP = 8, 4

# Every read phase of the sample's own requests. ACT is recorded on the
# request that made the controller open the row; most requests have none.
QUERY = """
SELECT t.ID, t.Address, t.TimeOfGeneration, p.PhaseName, p.PhaseBegin,
       p.DataStrobeBegin, p.DataStrobeEnd, p.Rank, p.BankGroup, p.Bank, p.Row, p.Column
FROM Transactions AS t
JOIN Phases AS p ON p.Transact = t.ID
WHERE t.Command = 'R'
  AND t.TimeOfGeneration >= ? AND t.TimeOfGeneration < ?
  AND t.Address >= ? AND t.Address < ?
  AND p.PhaseName IN ('ACT', 'RD', 'RDA', 'RESP')
ORDER BY t.ID
"""


def trace(connection, row, window=None):
    """Requests of one sample, in the order they reached the controller.

    window = (first, end) keeps only the requests whose address, relative to
    the start of the sample, falls in [first, end).
    """
    lower = row["hbm_offset"]
    requests = {}
    for (
        ident,
        address,
        generated,
        phase,
        begin,
        strobe_begin,
        strobe_end,
        *where,
    ) in connection.execute(
        QUERY, (row["start_ps"], row["end_ps"], lower, lower + row["hbm_span"])
    ):
        request = requests.setdefault(
            ident, {"address": address - lower, "generated_ps": generated, "activate_ps": ""}
        )
        if phase == "ACT":
            request["activate_ps"] = begin
        elif phase == "RESP":
            request["response_ps"] = begin
        else:
            pseudo_channel, bank_group, bank, row_index, column = where
            request.update(
                read_command_ps=begin,
                burst_begin_ps=strobe_begin,
                burst_end_ps=strobe_end,
                pseudo_channel=pseudo_channel,
                bank_group=bank_group % BANK_GROUPS,
                bank=bank % BANKS_PER_GROUP,
                row=row_index,
                column=column,
            )
    ordered = sorted(requests.values(), key=lambda r: (r["generated_ps"], r["address"]))
    # Blocks below 32 B still cost one whole 32 B request per copy.
    expected = row["repetitions"] * max(1, row["bytes"] // 32)
    if window:
        first, end = window
        ordered = [r for r in ordered if first <= r["address"] < end]
        expected = (end - first) // 32
    if len(ordered) != expected or any("burst_begin_ps" not in r for r in ordered):
        raise ValueError(f"Incomplete trace for {row['bytes']} B sample {row['sample']}")
    return [
        {"bytes": row["bytes"], "sample": row["sample"], "request": index, **request}
        for index, request in enumerate(ordered)
    ]


SPACING_COLUMNS = (
    "bytes",
    "sample",
    "rule",
    "gap_ns",
    "from_bank_group",
    "to_bank_group",
    "overlap",
    "count",
)


def bursts(requests, pseudo_channel):
    """Bursts of one pseudo-channel in bus order, each with its spacing from the previous one.

    rule is L when both bursts are in the same bank group (tCCD_L applies),
    S otherwise (tCCD_S); overlap means the burst started before the previous
    one had left the data bus.
    """
    ordered = sorted(
        (r for r in requests if r["pseudo_channel"] == pseudo_channel),
        key=lambda r: r["burst_begin_ps"],
    )
    result = [dict(ordered[0])] if ordered else []
    for previous, request in zip(ordered, ordered[1:]):
        result.append(
            {
                **request,
                "gap_ps": request["burst_begin_ps"] - previous["burst_begin_ps"],
                "rule": "L" if previous["bank_group"] == request["bank_group"] else "S",
                "from_bank_group": previous["bank_group"],
                "overlap": request["burst_begin_ps"] < previous["burst_end_ps"],
            }
        )
    return result


def spacing(requests):
    """Spacing counts of one sample, over both pseudo-channels."""
    counts = {}
    for pseudo_channel in sorted({r["pseudo_channel"] for r in requests}):
        for burst in bursts(requests, pseudo_channel)[1:]:
            key = (
                burst["rule"],
                burst["gap_ps"] / 1000,
                burst["from_bank_group"],
                burst["bank_group"],
                int(burst["overlap"]),
            )
            counts[key] = counts.get(key, 0) + 1
    first = requests[0]
    return [
        {
            "bytes": first["bytes"],
            "sample": first["sample"],
            **dict(zip(SPACING_COLUMNS[2:], key)),
            "count": count,
        }
        for key, count in sorted(counts.items())
    ]


def size_spec(text):
    """SIZE or SIZE:FIRST:END -> (size, window or None)."""
    parts = [int(part, 0) for part in text.split(":")]
    if len(parts) not in (1, 3):
        raise argparse.ArgumentTypeError("expected SIZE or SIZE:FIRST:END")
    return parts[0], tuple(parts[1:]) or None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("log", type=Path)
    parser.add_argument("database_dir", type=Path)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--size", type=size_spec, action="append", help="block size to extract, SIZE[:FIRST:END]"
    )
    choice.add_argument("--spacing", action="store_true", help="burst spacing of every sample")
    parser.add_argument("--sample", type=int, default=0, help="sample index (default: 0)")
    args = parser.parse_args()
    try:
        rows = measurements(args.log.read_text())
        if args.size:
            by_size = {r["bytes"]: r for r in rows if r["sample"] == args.sample}
            if any(size not in by_size for size, _ in args.size):
                raise ValueError("Unknown block size or sample")
            rows = [(by_size[size], window) for size, window in args.size]
        paths = list(args.database_dir.glob("DRAMSysRecordable0_*.tdb"))
        if len(paths) != 1:
            raise ValueError(f"Expected one channel 0 database, found {len(paths)}")
        uri = paths[0].resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            if args.spacing:
                columns = SPACING_COLUMNS
                output = [line for row in rows for line in spacing(trace(connection, row))]
            else:
                columns = COLUMNS
                output = [r for row, window in rows for r in trace(connection, row, window)]
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.DictWriter(sys.stdout, fieldnames=columns)
    writer.writeheader()
    writer.writerows(output)


if __name__ == "__main__":
    main()
