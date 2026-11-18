#!/usr/bin/env python3
"""Plot bandwidth and DMA copy counts around the 1 MiB TCDM boundary."""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "assets/bandwidth.png"
    )
    args = parser.parse_args()
    with args.csv.open() as source:
        rows = list(csv.DictReader(source))
    sizes = sorted({int(row["bytes"]) // 1024 for row in rows})
    detail_sizes = [size for size in sizes if 1020 <= size <= 1036]
    if sizes != [512, 1020, 1024, 1028, 1032, 1036, 2048]:
        raise ValueError("Expected the complete seven-size threshold sweep")

    # A full linear axis cannot separate labels only 4 KiB apart. A second
    # column shows those same measured points with a narrower linear range.
    fig, axes = plt.subplots(
        2,
        2,
        figsize=(13, 7),
        sharex="col",
        gridspec_kw={"height_ratios": [1.4, 1]},
        layout="constrained",
    )
    detail_rates = []
    for limit, color in ((256 * 1024, "#287f8e"), (1024 * 1024, "#8660a8")):
        points = sorted(
            (row for row in rows if int(row["dma_limit_bytes"]) == limit),
            key=lambda row: int(row["bytes"]),
        )
        if len(points) != len(sizes):
            raise ValueError(f"Missing measurements for DMA limit {limit}")
        label = "Up to 256 KiB per copy" if limit == 256 * 1024 else "Up to 1 MiB per copy"
        for column in (0, 1):
            selected = (
                points
                if column == 0
                else [row for row in points if int(row["bytes"]) // 1024 in detail_sizes]
            )
            x = [int(row["bytes"]) / 1024 for row in selected]
            rates = [float(row["median_GB_per_s"]) for row in selected]
            axes[0, column].plot(
                x, rates, "o-", color=color, label=label, linewidth=1.8, markersize=5
            )
            axes[1, column].plot(
                x,
                [int(row["dma_commands"]) for row in selected],
                "o-",
                color=color,
                linewidth=1.8,
                markersize=5,
            )
            if column == 1:
                detail_rates.extend(rates)
            for size, rate in zip(x, rates):
                if column == 0 and size not in (x[0], x[-1]):
                    continue
                axes[0, column].annotate(
                    f"{rate:.3f}",
                    (size, rate),
                    xytext=(0, -12 if limit == 256 * 1024 else 9),
                    textcoords="offset points",
                    ha="center",
                    va="top" if limit == 256 * 1024 else "bottom",
                    color=color,
                    fontsize=8,
                )

    peak = float(rows[0]["nominal_peak_GB_per_s"])
    axes[0, 0].axhline(peak, color="#ba6540", linestyle="--", label="Theoretical bandwidth")
    axes[0, 0].set(title="Full range", ylim=(0, peak * 1.1))
    axes[0, 0].set_yticks([0, 16, 32, 48, peak])
    axes[0, 0].legend(loc="lower right", fontsize=9)
    axes[0, 1].set(
        title="Detail near 1024 KiB (zoomed axes)",
        ylim=(min(detail_rates) - 0.12, max(detail_rates) + 0.12),
    )
    axes[1, 0].set(ylim=(0, 9), yticks=[0, 1, 2, 4, 5, 8])
    axes[1, 1].set(ylim=(0, 6), yticks=[0, 1, 2, 4, 5])
    axes[1, 0].set_xticks([512, 1024, 2048], ["512", "1024", "2048"])
    axes[1, 1].set_xticks(detail_sizes, [str(size) for size in detail_sizes])
    for column in (0, 1):
        axes[0, column].set_ylabel("Useful bandwidth (GB/s)")
        axes[1, column].set(ylabel="DMA copies per dataset", xlabel="Total dataset size (KiB)")
        for axis in axes[:, column]:
            axis.axvline(1024, color="#777777", linestyle=":", linewidth=1)
            axis.grid(True, alpha=0.2)
            axis.spines[["top", "right"]].set_visible(False)
    axes[1, 0].text(1050, 8, "TCDM capacity: 1024 KiB", fontsize=9, color="#555555")
    fig.suptitle("Local HBM reads around the 1 MiB TCDM limit", fontsize=14)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180)
    fig.savefig(args.output.with_suffix(".svg"))
    plt.close(fig)
    print(args.output)


if __name__ == "__main__":
    main()
