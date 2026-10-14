"""Offline-only executable API boundary. Nothing here makes a real network call."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from kpi_investigator.analysis import Report
from kpi_investigator.consent import ConsentBinding, canonical_json, valid_consent
from kpi_investigator.data import MAX_CHANNELS
from kpi_investigator.interpretation import TEMPLATES, UNCERTAINTIES, validated_interpretation

MODEL = "gpt-6.1-sol"
MAX_OUTPUT_TOKENS = 3000
MAX_INPUT_CHARS = 12_000  # conservative bounded proxy; actual token count requires provider validation


class SafeSendRejected(Exception):
    pass


@dataclass(frozen=True)
class BudgetAttestation:
    """Represents separate Owner/admin readback, never inferred from an app secret."""
    hard_limit_on: bool = False
    project_key_confirmed: bool = False
    usage_trial_confirmed: bool = False
    amount_usd: int | None = None
    review_valid: bool = False

    def allows_live(self) -> bool:
        return (self.hard_limit_on and self.project_key_confirmed and
                self.usage_trial_confirmed and self.amount_usd == 15 and self.review_valid)


def _direction(delta: int | float | None) -> str:
    return "UNKNOWN" if delta is None else ("UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT")


def _bucket(change_percent: float | None) -> str:
    if change_percent is None:
        return "UNDEFINED_BASE"
    amount = abs(change_percent)
    return "ZERO" if amount == 0 else "SMALL" if amount < 10 else "MEDIUM" if amount < 30 else "LARGE"


def _unsafe_differencing(report: Report) -> bool:
    """Conservatively reject small and complementary channel/product cohorts."""
    d = report.context.dataset
    baseline, target = report.context.baseline, report.context.target
    for month in (baseline, target):
        rows = [r for r in d.lines if r.day.strftime("%Y-%m") == month]
        universe = {r.order_id for r in rows}
        if len(universe) < 10:
            return True
        for field in ("channel", "product"):
            names = {getattr(r, field) for r in rows}
            if len(names) > 1:
                for name in names:
                    cohort = {r.order_id for r in rows if getattr(r, field) == name}
                    if 0 < len(cohort) < 10 or 0 < len(universe - cohort) < 10:
                        return True
    return False


def build_api_payload(report: Report) -> dict[str, Any]:
    """Construct a narrow, fixed-field JSON object without raw rows or user labels."""
    if not report.context.comparable:
        raise SafeSendRejected("月次比較が安全に行えません。")
    if not report.context.dataset.is_demo and _unsafe_differencing(report):
        raise SafeSendRejected("少数集計または差分復元の危険があるため送信できません。")
    trends = []
    metrics = report.sections["S02_PERIOD_COMPARISON"]["metrics"]
    for metric, tag in (("revenue", "REVENUE"), ("orders", "ORDERS"), ("sessions", "SESSIONS")):
        trends.append({"metric_id": tag, "direction": _direction(metrics[metric]["difference"]),
                       "change_band": _bucket(metrics[metric]["change_percent"])})
    # Only aggregated coarse labels; omit exact amounts, names, identifiers and individual days.
    prompt = canonical_json({"type": "COARSE_MONTHLY_TRENDS", "trends": trends})
    if len(prompt) > MAX_INPUT_CHARS:
        raise SafeSendRejected("送信対象の情報量が上限を超えています。")
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["interpretation_type", "fact_ids", "uncertainty_type"],
        "properties": {
            "interpretation_type": {"type": "string", "enum": sorted(TEMPLATES)},
            "fact_ids": {"type": "array", "items": {"type": "string", "enum": sorted(report.facts)}, "maxItems": MAX_CHANNELS},
            "uncertainty_type": {"type": "string", "enum": sorted(UNCERTAINTIES)},
        },
    }
    return {
        "model": MODEL, "store": False, "background": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS, "reasoning": {"effort": "medium"},
        "input": [{"role": "system", "content": "Select a code from the schema only. Do not assert causal explanations."},
                  {"role": "user", "content": prompt}],
        "text": {"format": {"type": "json_schema", "name": "kpi_interpretation", "strict": True, "schema": schema}},
    }


def validate_api_payload(report: Report, payload: dict[str, Any]) -> bool:
    """Verify every transmitted field by comparing against a fresh canonical build."""
    try:
        return (payload == build_api_payload(report) and payload["store"] is False
                and payload["background"] is False and payload["model"] == MODEL)
    except (ValueError, KeyError, SafeSendRejected, TypeError):
        return False


class Transport(Protocol):
    max_retries: int

    def send(self, body: dict[str, Any]) -> dict[str, Any]: ...


class MockTransport:
    """Captures the exact API body; never imports or connects to OpenAI."""
    max_retries = 0

    def __init__(self, *, answer: dict[str, Any] | None = None, failure: bool = False):
        self.calls: list[dict[str, Any]] = []
        self.answer = answer if answer is not None else {
            "interpretation_type": "INSUFFICIENT_TO_IDENTIFY_CAUSE", "fact_ids": [], "uncertainty_type": "CAUSE_UNKNOWN"
        }
        self.failure = failure

    def send(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(body)
        if self.failure:
            raise TimeoutError("INTERNAL_SECRET_MUST_NOT_BE_DISPLAYED")
        return self.answer


def mocked_ai_attempt(
    report: Report, *, raw_traffic: bytes, raw_orders: bytes, request_id: str,
    anonymous_browser_id: str, quota: Any, transport: Transport,
    budget: BudgetAttestation, user_consent: ConsentBinding | None = None,
    allow_mock_execution: bool = False,
    now: datetime | None = None,
) -> tuple[str | None, str]:
    """Test the full safety pipeline with mock-only transport, fail closed by default."""
    if not allow_mock_execution or not budget.allows_live():
        return None, "LIVE_OFF"
    try:
        payload = build_api_payload(report)
        if not validate_api_payload(report, payload):
            return None, "INVALID_PAYLOAD"
        if not report.context.dataset.is_demo and not valid_consent(
            user_consent, raw_traffic, raw_orders, report.context.target, payload
        ):
            return None, "NO_CONSENT"
        if not request_id or not anonymous_browser_id or getattr(transport, "max_retries", None) != 0:
            return None, "UNSAFE_OPTIONS"
        outcome = quota.reserve(anonymous_browser_id, request_id, now=now)
        if outcome != "RESERVED":
            return None, outcome
        # This exact JSON is what the consent preview shows and MockTransport captures.
        output = transport.send(payload)
        text = validated_interpretation(report, output)
        if text is None:
            return None, "INVALID_AI_OUTPUT"
        return text, "MOCK_VALIDATED"
    except SafeSendRejected:
        return None, "PRIVACY_REJECTED"
    except Exception:
        # No exception text, user data, payload or key is ever printed or logged.
        return None, "MOCK_FAILED"
