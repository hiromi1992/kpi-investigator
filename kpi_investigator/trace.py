"""Measured execution events with version-bound, allowlisted real source views."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from kpi_investigator import ANALYSIS_VERSION

T = TypeVar("T")
ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "code_manifest.json"

@dataclass(frozen=True)
class TraceEvent:
    function_id: str
    description: str
    elapsed_ms: float
    source_sha256: str
    analysis_version: str = ANALYSIS_VERSION


# Approved identifiers only; never resolve arbitrary user paths.
SOURCE_ALLOWLIST: dict[str, tuple[str, str]] = {
    "load_dataset": ("kpi_investigator.data", "load_dataset"),
    "S01_KPI_SUMMARY": ("kpi_investigator.analysis", "s01_kpi_summary"),
    "S02_PERIOD_COMPARISON": ("kpi_investigator.analysis", "s02_period_comparison"),
    "S03_CHANNEL": ("kpi_investigator.analysis", "s03_channel"),
    "S04_PRODUCT": ("kpi_investigator.analysis", "s04_product"),
    "S05_GROWTH_DECLINE": ("kpi_investigator.analysis", "s05_growth_decline"),
    "S06_REVENUE_BRIDGE": ("kpi_investigator.analysis", "s06_revenue_bridge"),
    "S07_DAILY_CHANGE": ("kpi_investigator.analysis", "s07_daily_change"),
}


def _get_actual_source(identifier: str) -> str:
    import importlib
    if identifier not in SOURCE_ALLOWLIST:
        raise ValueError("公開できないコード参照")
    module_name, function_name = SOURCE_ALLOWLIST[identifier]
    module = importlib.import_module(module_name)
    fn = getattr(module, function_name)
    path = Path(inspect.getfile(fn)).resolve()
    if path.parent != ROOT / "kpi_investigator":
        raise ValueError("コードの参照先が不正")
    return inspect.getsource(fn)


def _hash_source(identifier: str) -> str:
    return hashlib.sha256(_get_actual_source(identifier).encode("utf-8")).hexdigest()


def verify_source_manifest(identifier: str) -> str | None:
    """Expose only exact real function source matching the pinned release manifest."""
    try:
        manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
        if manifest["analysis_version"] != ANALYSIS_VERSION:
            return None
        expected = manifest["functions"][identifier]
        source = _get_actual_source(identifier)
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected:
            return None
        return source
    except (ValueError, KeyError, FileNotFoundError, OSError, AttributeError, TypeError):
        return None


def measured(identifier: str, description: str, events: list[TraceEvent], task: Callable[[], T]) -> T:
    """Record actual wall-clock duration only after a function executes."""
    start = time.perf_counter_ns()
    result = task()
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    events.append(TraceEvent(identifier, description, elapsed_ms, _hash_source(identifier)))
    return result


def inspect_trace(event: TraceEvent) -> str | None:
    source = verify_source_manifest(event.function_id)
    if source is None or hashlib.sha256(source.encode("utf-8")).hexdigest() != event.source_sha256:
        return None
    if event.analysis_version != ANALYSIS_VERSION:
        return None
    return source
