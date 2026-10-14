"""Strict CSV contracts and structural completeness; never log raw input values."""

from __future__ import annotations

import calendar
import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 20_000
MAX_CHANNELS = 20
MAX_PRODUCTS = 200
MAX_LABEL_LENGTH = 100
TRAFFIC_COLUMNS = ("date", "channel", "sessions")
ORDER_COLUMNS = ("order_id", "date", "channel", "product", "quantity", "revenue")


class DataQualityError(ValueError):
    """Safe, non-sensitive validation error exposed to the UI."""

    def __init__(self, code: str, description: str = "入力形式を確認してください。") -> None:
        self.code = code
        self.public_message = f"{description}（{code}）"
        super().__init__(self.public_message)


@dataclass(frozen=True)
class Traffic:
    day: date
    channel: str
    sessions: int


@dataclass(frozen=True)
class OrderLine:
    order_id: str
    day: date
    channel: str
    product: str
    quantity: int
    revenue: int


@dataclass(frozen=True)
class Dataset:
    traffic: tuple[Traffic, ...]
    lines: tuple[OrderLine, ...]
    is_demo: bool


def _read_csv(raw: bytes, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not raw or len(raw) > MAX_CSV_BYTES:
        raise DataQualityError("SIZE", "CSVの容量が規定外です。")
    try:
        content = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != columns:
            raise DataQualityError("HEADERS", "CSVの列名・列順が仕様と異なります。")
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise DataQualityError("ROWS", "CSVの行数上限を超えています。")
            if None in row or any(v is None for v in row.values()):
                raise DataQualityError("ROW_SHAPE", "CSVの列数が一致しない行があります。")
            rows.append(row)
        if not rows:
            raise DataQualityError("EMPTY", "データ行がありません。")
        return rows
    except DataQualityError:
        raise
    except (UnicodeError, csv.Error):
        raise DataQualityError("CSV_PARSE", "CSVを読み込めませんでした。") from None


def _date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DataQualityError("DATE", "日付はYYYY-MM-DD形式にしてください。")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise DataQualityError("DATE", "日付に不正な値があります。") from None
    if parsed.isoformat() != value:
        raise DataQualityError("DATE", "日付の書式が異なります。")
    return parsed


def _count(value: str, *, positive: bool = False) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise DataQualityError("INTEGER", "数量・売上・訪問数には整数を指定してください。")
    num = int(value)
    if (positive and num < 1) or num > 1_000_000_000_000:
        raise DataQualityError("RANGE", "数量・売上・訪問数が許容範囲外です。")
    return num


def _label(value: str) -> str:
    if not value or len(value) > MAX_LABEL_LENGTH or any(ord(c) < 32 for c in value):
        raise DataQualityError("LABEL", "識別子・分類名の長さまたは形式が不正です。")
    return value


def load_dataset(traffic_raw: bytes, lines_raw: bytes, *, is_demo: bool = False) -> Dataset:
    """Read the 2 CSVs, refusing duplicate lines and inconsistent order attributes."""
    traffic_rows = _read_csv(traffic_raw, TRAFFIC_COLUMNS)
    line_rows = _read_csv(lines_raw, ORDER_COLUMNS)
    traffic: list[Traffic] = []
    lines: list[OrderLine] = []
    seen_traffic: set[tuple[date, str]] = set()
    seen_lines: set[tuple[str, date, str, str, int, int]] = set()
    order_meta: dict[str, tuple[date, str]] = {}
    channels: set[str] = set()
    products: set[str] = set()
    for raw in traffic_rows:
        item = Traffic(_date(raw["date"]), _label(raw["channel"]), _count(raw["sessions"]))
        key = (item.day, item.channel)
        if key in seen_traffic:
            raise DataQualityError("DUP_TRAFFIC", "日付とチャネルが重複しています。")
        seen_traffic.add(key)
        channels.add(item.channel)
        traffic.append(item)
    for raw in line_rows:
        item = OrderLine(
            _label(raw["order_id"]), _date(raw["date"]), _label(raw["channel"]),
            _label(raw["product"]), _count(raw["quantity"], positive=True), _count(raw["revenue"]),
        )
        meta = (item.day, item.channel)
        if item.order_id in order_meta and order_meta[item.order_id] != meta:
            raise DataQualityError("ORDER_CONFLICT", "同じ注文番号で日付・チャネルが異なっています。")
        order_meta[item.order_id] = meta
        key = (item.order_id, item.day, item.channel, item.product, item.quantity, item.revenue)
        if key in seen_lines:
            raise DataQualityError("DUP_LINE", "同一の注文明細が重複しています。")
        seen_lines.add(key)
        channels.add(item.channel)
        products.add(item.product)
        lines.append(item)
    if len(channels) > MAX_CHANNELS or len(products) > MAX_PRODUCTS:
        raise DataQualityError("CARDINALITY", "チャネルまたは商品の種類数が上限を超えています。")
    if not all(item.channel in {row.channel for row in traffic} for item in lines):
        raise DataQualityError("UNKNOWN_CHANNEL", "注文データに訪問データ側にないチャネルがあります。")
    return Dataset(tuple(traffic), tuple(lines), is_demo)


def prior_month(target: str) -> str:
    """Compute previous calendar month; never guess from available observations."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", target):
        raise DataQualityError("MONTH", "対象月の形式が異なります。")
    year, month = map(int, target.split("-"))
    if year < 2:
        raise DataQualityError("MONTH", "対象月の形式が異なります。")
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def assert_contiguous(baseline: str, target: str) -> None:
    """Explicit comparison API rejects gaps and reverse order."""
    if baseline != prior_month(target):
        raise DataQualityError("NOT_PREVIOUS_MONTH", "前月比は連続した昇順の2暦月に限ります。")


def available_months(dataset: Dataset) -> list[str]:
    return sorted({r.day.strftime("%Y-%m") for r in dataset.traffic} | {r.day.strftime("%Y-%m") for r in dataset.lines})


def complete_months(dataset: Dataset, baseline: str, target: str) -> tuple[bool, tuple[str, ...]]:
    """Prove only structural day×observed-channel coverage of both calendar months."""
    assert_contiguous(baseline, target)
    channels = {r.channel for r in dataset.traffic} | {r.channel for r in dataset.lines}
    traffic_keys = {(r.day, r.channel) for r in dataset.traffic}
    missing: list[str] = []
    for value in (baseline, target):
        year, month = map(int, value.split("-"))
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            point = date(year, month, day)
            for channel in channels:
                if (point, channel) not in traffic_keys:
                    # Never leak a user-provided channel label into an error.
                    missing.append(point.isoformat())
    return not missing, tuple(sorted(set(missing)))
