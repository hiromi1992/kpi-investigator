"""Reproducible entirely synthetic EC CSVs: Aug 800,000 JPY -> Sep 670,000 JPY."""

from __future__ import annotations

import calendar
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def build() -> None:
    traffic = []
    lines = []
    channels = ("検索", "広告")
    products = ("定番セット", "季節セット")
    budgets = {(8, "検索"): 600_000, (8, "広告"): 200_000,
               (9, "検索"): 450_000, (9, "広告"): 220_000}
    for month in (8, 9):
        length = calendar.monthrange(2026, month)[1]
        for day in range(1, length + 1):
            for c in channels:
                traffic.append({"date": f"2026-{month:02d}-{day:02d}", "channel": c,
                                "sessions": 100 + day + (10 if c == "広告" else 0)})
        for c in channels:
            revenue = budgets[(month, c)]
            for n in range(12):
                order_id = f"SYN-{month:02d}-{c}-{n+1:02d}"
                date = f"2026-{month:02d}-{n+1:02d}"
                allocation = revenue // 12 + (1 if n < revenue % 12 else 0)
                first = allocation // 2
                second = allocation - first
                for product, amount in zip(products, (first, second)):
                    lines.append({"order_id": order_id, "date": date, "channel": c,
                                  "product": product, "quantity": 1, "revenue": amount})
    ROOT.mkdir(parents=True, exist_ok=True)
    for path, names, content in ((ROOT / "traffic.csv", ("date", "channel", "sessions"), traffic),
                                 (ROOT / "order_lines.csv", ("order_id", "date", "channel", "product", "quantity", "revenue"), lines)):
        with path.open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=names, lineterminator="\n")
            writer.writeheader()
            writer.writerows(content)


if __name__ == "__main__":
    build()
