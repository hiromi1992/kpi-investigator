"""Focused I3 regression cases: malformed interpretations and 20-channel contract."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from kpi_investigator.ai import BudgetAttestation, MockTransport, build_api_payload, mocked_ai_attempt
from kpi_investigator.analysis import build_report
from kpi_investigator.data import DataQualityError, MAX_CHANNELS
from kpi_investigator.interpretation import (
    load_pregenerated, validated_interpretation, verified_pregenerated,
)
from kpi_investigator.quota import MockAtomicQuota

ROOT = Path(__file__).resolve().parents[1]
TRAFFIC = (ROOT / "fixtures" / "traffic.csv").read_bytes()
ORDERS = (ROOT / "fixtures" / "order_lines.csv").read_bytes()


def demo_report():
    return build_report(TRAFFIC, ORDERS, target="2026-09", is_demo=True)


@pytest.mark.parametrize("field,bad", [
    ("interpretation_type", []),
    ("interpretation_type", {}),
    ("interpretation_type", ["OVERALL_REVENUE_DOWN"]),
    ("interpretation_type", 17),
    ("uncertainty_type", []),
    ("uncertainty_type", {}),
    ("uncertainty_type", ["CAUSE_UNKNOWN"]),
    ("uncertainty_type", 17),
])
def test_i3_f01_malformed_enum_fails_closed(field, bad):
    choice = {"interpretation_type": "INSUFFICIENT_TO_IDENTIFY_CAUSE",
              "fact_ids": [], "uncertainty_type": "CAUSE_UNKNOWN"}
    choice[field] = bad
    assert validated_interpretation(demo_report(), choice) is None


@pytest.mark.parametrize("bad", [[], {}, 17])
def test_i3_f01_malformed_pregenerated_entirely_rejected(bad):
    r = demo_report()
    manifest = copy.deepcopy(load_pregenerated())
    assert manifest is not None
    assert verified_pregenerated(r, manifest, traffic_raw=TRAFFIC, lines_raw=ORDERS)
    manifest["interpretations"][0]["selection"]["interpretation_type"] = bad
    assert verified_pregenerated(r, manifest, traffic_raw=TRAFFIC, lines_raw=ORDERS) is None


def test_i3_f01_malformed_mock_response_falls_back_to_python():
    r = demo_report()
    assert r.sections["S01_KPI_SUMMARY"]["2026-09"]["revenue"] == 670_000
    t = MockTransport(answer={
        "interpretation_type": ["OVERALL_REVENUE_DOWN"],
        "fact_ids": ["OVERALL_REVENUE_DELTA"],
        "uncertainty_type": "CAUSE_UNKNOWN",
    })
    output = mocked_ai_attempt(
        r, raw_traffic=TRAFFIC, raw_orders=ORDERS,
        request_id="i3-incorrect-response", anonymous_browser_id="anon-i3",
        quota=MockAtomicQuota(), transport=t,
        budget=BudgetAttestation(True, True, True, 15, True),
        allow_mock_execution=True,
    )
    assert output == (None, "INVALID_AI_OUTPUT")
    assert len(t.calls) == 1
    # Python-derived facts remain unchanged; no second transport call is attempted.
    assert r.sections["S01_KPI_SUMMARY"]["2026-09"]["revenue"] == 670_000


def multichannel_raw(count: int) -> tuple[bytes, bytes]:
    channels = [f"CH{idx:02d}" for idx in range(count)]
    traffic = ["date,channel,sessions"]
    orders = ["order_id,date,channel,product,quantity,revenue"]
    for mon, days in (("08", 31), ("09", 30)):
        for day in range(1, days + 1):
            for name in channels:
                traffic.append(f"2026-{mon}-{day:02d},{name},10")
        for idx, name in enumerate(channels):
            revenue = 1000 if mon == "08" else 1100
            orders.append(f"S{mon}-{idx:02d},2026-{mon}-01,{name},P,1,{revenue}")
    return ("\n".join(traffic) + "\n").encode(), ("\n".join(orders) + "\n").encode()


@pytest.mark.parametrize("channels", [11, 20])
def test_i3_f02_schema_can_represent_every_accepted_channel(channels):
    a, b = multichannel_raw(channels)
    r = build_report(a, b, target="2026-09", is_demo=True)
    assert r.context.comparable
    fact_ids = sorted(key for key in r.facts if key.startswith("CHANNEL_REVENUE_DELTA_"))
    assert len(fact_ids) == channels
    p = build_api_payload(r)
    prop = p["text"]["format"]["schema"]["properties"]["fact_ids"]
    assert prop["maxItems"] == MAX_CHANNELS
    assert prop["maxItems"] >= len(fact_ids)
    assert set(fact_ids) <= set(prop["items"]["enum"])
    assert validated_interpretation(r, {
        "interpretation_type": "CHANNEL_VARIATION_OBSERVED",
        "fact_ids": fact_ids,
        "uncertainty_type": "CAUSE_UNKNOWN",
    }) is not None


def test_i3_f02_twenty_one_channels_refused_before_api_schema():
    assert MAX_CHANNELS == 20  # bound to the approved input contract
    a, b = multichannel_raw(21)
    with pytest.raises(DataQualityError, match="CARDINALITY"):
        build_report(a, b, target="2026-09", is_demo=True)
