"""Seven independent Python computations; AI cannot change a number or fact."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from kpi_investigator import ANALYSIS_VERSION
from kpi_investigator.data import Dataset, DataQualityError, available_months, complete_months, load_dataset, prior_month
from kpi_investigator.trace import TraceEvent, measured

METRIC_UNITS = {
    "revenue": "円", "orders": "件", "quantity": "点", "sessions": "回",
    "average_order_value": "円/注文", "orders_per_session": "注文/セッション",
}
METRIC_LABELS = {
    "revenue": "売上", "orders": "注文数", "quantity": "販売数量", "sessions": "セッション",
    "average_order_value": "平均注文額", "orders_per_session": "セッションあたり注文数",
}


@dataclass
class AnalysisContext:
    dataset: Dataset
    target: str
    baseline: str
    comparable: bool
    missing_days: tuple[str, ...]
    cache: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    context: AnalysisContext
    sections: dict[str, Any]
    facts: dict[str, dict[str, Any]]
    important_insights: list[dict[str, Any]]
    events: list[TraceEvent]
    analysis_version: str = ANALYSIS_VERSION


def _rows_for_month(dataset: Dataset, month: str):
    return ([t for t in dataset.traffic if t.day.strftime("%Y-%m") == month],
            [r for r in dataset.lines if r.day.strftime("%Y-%m") == month])


def _stats(traffic: list[Any], lines: list[Any]) -> dict[str, int | float | None]:
    revenue = sum(r.revenue for r in lines)
    orders = len({r.order_id for r in lines})
    sessions = sum(t.sessions for t in traffic)
    quantity = sum(r.quantity for r in lines)
    return {"revenue": revenue, "orders": orders, "quantity": quantity,
            "sessions": sessions, "average_order_value": revenue / orders if orders else None,
            "orders_per_session": orders / sessions if sessions else None}


def s01_kpi_summary(ctx: AnalysisContext) -> dict[str, dict[str, int | float | None]]:
    """Count distinct orders, not order lines, for each observed calendar month."""
    output = {}
    for month in (ctx.baseline, ctx.target):
        output[month] = _stats(*_rows_for_month(ctx.dataset, month))
    return output


def s02_period_comparison(ctx: AnalysisContext) -> dict[str, Any]:
    """Compute month-over-month differences only when structural coverage is proven."""
    if not ctx.comparable:
        return {"comparable": False, "metrics": {}, "reason": "両月の日×チャネル行が不完全です。前月比は表示しません。"}
    monthly = ctx.cache["S01_KPI_SUMMARY"]
    baseline, target = monthly[ctx.baseline], monthly[ctx.target]
    metrics: dict[str, Any] = {}
    for name in METRIC_LABELS:
        a, b = baseline[name], target[name]
        if a is None or b is None:
            metrics[name] = {"baseline": a, "target": b, "difference": None, "change_percent": None}
            continue
        delta = b - a
        metrics[name] = {"baseline": a, "target": b, "difference": delta,
                         "change_percent": delta / a * 100 if a != 0 else None}
    return {"comparable": True, "metrics": metrics, "reason": None}


def s03_channel(ctx: AnalysisContext) -> dict[str, Any]:
    """Calculate channel-specific revenue and visits without inventing missing rows."""
    channels = sorted({r.channel for r in ctx.dataset.traffic} | {r.channel for r in ctx.dataset.lines})
    rows: list[dict[str, Any]] = []
    for channel in channels:
        entry: dict[str, Any] = {"channel": channel}
        for month, key in ((ctx.baseline, "baseline"), (ctx.target, "target")):
            traffic, lines = _rows_for_month(ctx.dataset, month)
            entry[key] = _stats([r for r in traffic if r.channel == channel],
                                [r for r in lines if r.channel == channel])
        entry["revenue_delta"] = (entry["target"]["revenue"] - entry["baseline"]["revenue"] if ctx.comparable else None)
        entry["sessions_delta"] = (entry["target"]["sessions"] - entry["baseline"]["sessions"] if ctx.comparable else None)
        rows.append(entry)
    return {"rows": rows, "comparable": ctx.comparable}


def s04_product(ctx: AnalysisContext) -> dict[str, Any]:
    """A product order count is distinct orders containing that product."""
    products = sorted({r.product for r in ctx.dataset.lines})
    rows: list[dict[str, Any]] = []
    for product in products:
        entry: dict[str, Any] = {"product": product}
        for month, key in ((ctx.baseline, "baseline"), (ctx.target, "target")):
            _, lines = _rows_for_month(ctx.dataset, month)
            selected = [r for r in lines if r.product == product]
            entry[key] = {"revenue": sum(r.revenue for r in selected),
                          "quantity": sum(r.quantity for r in selected),
                          "orders_with_product": len({r.order_id for r in selected})}
        entry["revenue_delta"] = (entry["target"]["revenue"] - entry["baseline"]["revenue"] if ctx.comparable else None)
        rows.append(entry)
    return {"rows": rows, "comparable": ctx.comparable}


def s05_growth_decline(ctx: AnalysisContext) -> dict[str, Any]:
    """Rank comparable revenue deltas *within* a dimension; never mix units."""
    if not ctx.comparable:
        return {"channels": {"growth": [], "decline": []}, "products": {"growth": [], "decline": []}}
    result = {}
    for key, source, label in (
        ("channels", "S03_CHANNEL", "channel"), ("products", "S04_PRODUCT", "product")
    ):
        rows = ctx.cache[source]["rows"]
        result[key] = {
            "growth": sorted(([{label: r[label], "delta_yen": r["revenue_delta"]} for r in rows if r["revenue_delta"] > 0]),
                             key=lambda r: (-r["delta_yen"], r[label])),
            "decline": sorted(([{label: r[label], "delta_yen": r["revenue_delta"]} for r in rows if r["revenue_delta"] < 0]),
                              key=lambda r: (r["delta_yen"], r[label])),
        }
    return result


def s06_revenue_bridge(ctx: AnalysisContext) -> dict[str, Any]:
    """Reconcile two independent revenue splits; never add channel+product totals."""
    if not ctx.comparable:
        return {"comparable": False, "reason": "月次比較不可のため差額の内訳を計算しません。"}
    full = ctx.cache["S02_PERIOD_COMPARISON"]["metrics"]["revenue"]["difference"]
    channels = {r["channel"]: r["revenue_delta"] for r in ctx.cache["S03_CHANNEL"]["rows"]}
    products = {r["product"]: r["revenue_delta"] for r in ctx.cache["S04_PRODUCT"]["rows"]}
    return {"comparable": True, "overall_delta": full, "by_channel": channels, "by_product": products,
            "channel_reconciled": sum(channels.values()) == full,
            "product_reconciled": sum(products.values()) == full,
            "note": "チャネル別と商品別は同じ売上差の別の分解軸です。両者を合算しません。"}


def s07_daily_change(ctx: AnalysisContext) -> list[dict[str, Any]]:
    """Compare adjacent observed complete days; daily revenue of zero is allowed."""
    channels = {r.channel for r in ctx.dataset.traffic} | {r.channel for r in ctx.dataset.lines}
    grouped = defaultdict(set)
    for item in ctx.dataset.traffic:
        grouped[item.day].add(item.channel)
    by_day: dict[date, int] = defaultdict(int)
    for item in ctx.dataset.lines:
        if item.day.strftime("%Y-%m") == ctx.target:
            by_day[item.day] += item.revenue
    observed = sorted(day for day, members in grouped.items()
                      if day.strftime("%Y-%m") == ctx.target and channels <= members)
    valid = set(observed)
    changes = []
    for day in observed:
        prev = day - timedelta(days=1)
        if prev in valid:
            changes.append({"date": day.isoformat(), "revenue": by_day[day],
                            "previous_revenue": by_day[prev], "difference": by_day[day] - by_day[prev]})
    return sorted(changes, key=lambda r: (-abs(r["difference"]), r["date"]))


STAGES: tuple[tuple[str, Any, str], ...] = (
    ("S01_KPI_SUMMARY", s01_kpi_summary, "月別KPIを計算"),
    ("S02_PERIOD_COMPARISON", s02_period_comparison, "前月比の可否と差額を計算"),
    ("S03_CHANNEL", s03_channel, "チャネル単位で集計"),
    ("S04_PRODUCT", s04_product, "商品単位で集計"),
    ("S05_GROWTH_DECLINE", s05_growth_decline, "増減ランキングを作成"),
    ("S06_REVENUE_BRIDGE", s06_revenue_bridge, "売上差額を検算"),
    ("S07_DAILY_CHANGE", s07_daily_change, "連続日の日次変化を比較"),
)


def _build_facts(ctx: AnalysisContext) -> dict[str, dict[str, Any]]:
    if not ctx.comparable:
        return {}
    metrics = ctx.cache["S02_PERIOD_COMPARISON"]["metrics"]
    facts = {}
    for metric in ("revenue", "orders", "sessions"):
        item = metrics[metric]
        if item["difference"] is not None:
            facts[f"OVERALL_{metric.upper()}_DELTA"] = {
                "metric": metric, "delta": item["difference"], "unit": METRIC_UNITS[metric],
                "text": f"{METRIC_LABELS[metric]}の前月差は{item['difference']:+,.0f}{METRIC_UNITS[metric]}です。",
            }
    for index, row in enumerate(ctx.cache["S03_CHANNEL"]["rows"], start=1):
        facts[f"CHANNEL_REVENUE_DELTA_{index:02d}"] = {
            "metric": "channel_revenue", "delta": row["revenue_delta"], "unit": "円", "label": row["channel"],
            "text": f"チャネル「{row['channel']}」の売上前月差は{row['revenue_delta']:+,}円です。",
        }
    return facts


def _select_insights(facts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    # Separate metrics are different observations, not independent causal contributions.
    insights = []
    for metric in ("revenue", "orders", "sessions"):
        name = f"OVERALL_{metric.upper()}_DELTA"
        fact = facts.get(name)
        if fact and fact["delta"] != 0:
            insights.append({"fact_id": name, "metric": metric,
                             "view": "全体KPIの観測変化（原因の特定ではありません）", "text": fact["text"]})
    return insights[:3]


def build_report(traffic_raw: bytes, lines_raw: bytes, *, target: str, is_demo: bool = False) -> Report:
    """Validate inputs, execute all seven measured functions and attach fixed facts."""
    events: list[TraceEvent] = []
    dataset = measured("load_dataset", "2つのCSVを検証", events,
                       lambda: load_dataset(traffic_raw, lines_raw, is_demo=is_demo))
    if target not in available_months(dataset):
        raise DataQualityError("MONTH_NOT_PRESENT", "選択した対象月の観測データがありません。")
    baseline = prior_month(target)
    comparable, missing = complete_months(dataset, baseline, target)
    ctx = AnalysisContext(dataset, target, baseline, comparable, missing)
    sections: dict[str, Any] = {}
    for identifier, fn, desc in STAGES:
        value = measured(identifier, desc, events, lambda fn=fn: fn(ctx))
        sections[identifier] = value
        ctx.cache[identifier] = value
    facts = _build_facts(ctx)
    return Report(ctx, sections, facts, _select_insights(facts), events)
