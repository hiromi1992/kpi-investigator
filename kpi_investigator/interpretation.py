"""The only text allowed for model-selected or prebuilt interpretations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from kpi_investigator import ANALYSIS_VERSION, TEMPLATE_VERSION
from kpi_investigator.analysis import Report

TEMPLATES = {
    "OVERALL_REVENUE_DOWN": "全体の売上が減少しています。根本原因はこの情報だけでは確定できません。",
    "MULTIPLE_METRICS_DOWN": "複数の指標に低下が見られます。ただし相互に関連するため、独立した原因とは限りません。",
    "CHANNEL_VARIATION_OBSERVED": "チャネル別の売上差額を集計しました。内訳を確認してください。",
    "INSUFFICIENT_TO_IDENTIFY_CAUSE": "原因・要因については、このデータだけでは判断できません。",
}
UNCERTAINTIES = {
    "CAUSE_UNKNOWN": "原因は特定できません。",
    "LIMITED_DATA": "今回の入力範囲を超えることは判断できません。",
}
EXPLANATION_KEYS = frozenset(("interpretation_type", "fact_ids", "uncertainty_type"))


def validated_interpretation(report: Report, proposal: Any) -> str | None:
    """Reject an entire model response unless its type, evidence and meaning agree."""
    if not report.context.comparable or not isinstance(proposal, dict) or set(proposal) != EXPLANATION_KEYS:
        return None
    kind, ids, uncertainty = (proposal.get("interpretation_type"), proposal.get("fact_ids"), proposal.get("uncertainty_type"))
    # JSON can contain arrays/objects here: reject them before dictionary membership.
    # A malformed interpretation must never interrupt the Python-only report.
    if not isinstance(kind, str) or not isinstance(uncertainty, str):
        return None
    if kind not in TEMPLATES or uncertainty not in UNCERTAINTIES:
        return None
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
        return None
    facts = report.facts
    if any(value not in facts for value in ids):
        return None
    eligible: set[str]
    if kind == "OVERALL_REVENUE_DOWN":
        eligible = {"OVERALL_REVENUE_DELTA"}
        if facts.get("OVERALL_REVENUE_DELTA", {}).get("delta", 0) >= 0 or set(ids) != eligible:
            return None
    elif kind == "MULTIPLE_METRICS_DOWN":
        eligible = {key for key in ("OVERALL_REVENUE_DELTA", "OVERALL_ORDERS_DELTA", "OVERALL_SESSIONS_DELTA")
                    if key in facts and facts[key]["delta"] < 0}
        if len(eligible) < 2 or len(ids) < 2 or not set(ids) <= eligible:
            return None
    elif kind == "CHANNEL_VARIATION_OBSERVED":
        eligible = {k for k in facts if k.startswith("CHANNEL_REVENUE_DELTA_")}
        # Equal differences or all zero are still valid; this template asserts no cross-channel difference.
        if not eligible or set(ids) != eligible:
            return None
    else:
        # No measured causal explanatory variables in the approved CSV schema.
        eligible = set()
        if ids:
            return None
    return TEMPLATES[kind] + " " + UNCERTAINTIES[uncertainty]


def demo_fingerprint(traffic_raw: bytes, lines_raw: bytes) -> str:
    return hashlib.sha256(b"traffic.csv:\x00" + traffic_raw + b"\x00order_lines.csv:\x00" + lines_raw).hexdigest()


def verified_pregenerated(
    report: Report, manifest: dict[str, Any], *, traffic_raw: bytes, lines_raw: bytes,
) -> list[str] | None:
    """Reject all stale or unsafe prebuilt explanations; never borrow one for a user CSV."""
    if not report.context.dataset.is_demo:
        return None
    if (manifest.get("schema_version") != "pregenerated/v1"
        or manifest.get("analysis_version") != ANALYSIS_VERSION
        or manifest.get("template_version") != TEMPLATE_VERSION
        or manifest.get("target_month") != report.context.target
        or manifest.get("demo_sha256") != demo_fingerprint(traffic_raw, lines_raw)
        or manifest.get("generation_origin") != "approved-deterministic-selection/no-api"):
        return None
    source_hashes = manifest.get("source_sha256", {})
    if not isinstance(source_hashes, dict) or set(source_hashes) != {"analysis.py", "data.py", "interpretation.py"}:
        return None
    source_dir = Path(__file__).resolve().parent
    for name, expected in source_hashes.items():
        try:
            if hashlib.sha256((source_dir / name).read_bytes()).hexdigest() != expected:
                return None
        except OSError:
            return None
    choices = manifest.get("interpretations")
    if not isinstance(choices, list) or not (1 <= len(choices) <= 3):
        return None
    result = []
    for item in choices:
        if not isinstance(item, dict) or set(item) != {"selection", "template_text"}:
            return None
        exact = validated_interpretation(report, item["selection"])
        if exact is None or item["template_text"] != exact:
            return None
        result.append(exact)
    return result


def load_pregenerated() -> dict[str, Any] | None:
    """Read only the bundled manifest (never a caller-selected path)."""
    path = Path(__file__).resolve().parents[1] / "fixtures" / "pregenerated.json"
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None
