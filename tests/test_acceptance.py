"""Local acceptance and negative fixtures bound to approved R01-R18 and R3 m1-m5."""

from __future__ import annotations

import copy
import csv
import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kpi_investigator.ai import (
    BudgetAttestation, MockTransport, SafeSendRejected, build_api_payload,
    mocked_ai_attempt, validate_api_payload,
)
from kpi_investigator.analysis import build_report
from kpi_investigator.consent import (
    CONSUMER_DISCLOSURE, bind_consent, canonical_json, valid_consent,
)
from kpi_investigator.data import (
    DataQualityError, assert_contiguous, load_dataset, prior_month,
)
from kpi_investigator.interpretation import (
    load_pregenerated, validated_interpretation, verified_pregenerated, demo_fingerprint,
)
from kpi_investigator.player import render_trace_html
from kpi_investigator.quota import (
    GLOBAL_LIMIT, JST, MockAtomicQuota, InMemorySharedState, RedisQuotaContract,
    day_key, seconds_until_next_jst_midnight,
)
from kpi_investigator.trace import inspect_trace, verify_source_manifest, SOURCE_ALLOWLIST, TraceEvent

ROOT = Path(__file__).resolve().parents[1]
A = (ROOT / "fixtures" / "traffic.csv").read_bytes()
B = (ROOT / "fixtures" / "order_lines.csv").read_bytes()
TEST_BUDGET = BudgetAttestation(True, True, True, 15, True)  # Mock fixture only, not production attestation.


def report(a=A, b=B, *, target="2026-09", demo=True):
    return build_report(a, b, target=target, is_demo=demo)


def rewrite_csv(raw: bytes, fieldnames: list[str], updater) -> bytes:
    original = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    updater(original)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader(); writer.writerows(original)
    return buffer.getvalue().encode("utf-8")


def scenario_ai(r, candidate=None, *, consent=None, a=A, b=B, budget=TEST_BUDGET, quota=None,
                request="request-1", anon="browser-1", allow=True, failure=False, **kw):
    trans = MockTransport(answer=candidate, failure=failure)
    engine = quota or MockAtomicQuota()
    result = mocked_ai_attempt(r, raw_traffic=a, raw_orders=b, request_id=request,
                               anonymous_browser_id=anon, quota=engine, transport=trans,
                               budget=budget, user_consent=consent, allow_mock_execution=allow, **kw)
    return result, trans, engine


def demo_candidate(kind: str, r=None):
    r = r or report()
    facts = sorted(k for k in r.facts if k.startswith("CHANNEL_REVENUE_DELTA_"))
    id_map = {
        "OVERALL_REVENUE_DOWN": ["OVERALL_REVENUE_DELTA"],
        "MULTIPLE_METRICS_DOWN": ["OVERALL_REVENUE_DELTA", "OVERALL_SESSIONS_DELTA"],
        "CHANNEL_VARIATION_OBSERVED": facts,
        "INSUFFICIENT_TO_IDENTIFY_CAUSE": [],
    }
    return {"interpretation_type": kind, "fact_ids": id_map[kind], "uncertainty_type": "CAUSE_UNKNOWN"}


def test_r02_schema_and_noninteger_and_missing_fields():
    with pytest.raises(DataQualityError, match="HEADERS"):
        load_dataset(A.replace(b"sessions", b"visits"), B)
    with pytest.raises(DataQualityError, match="INTEGER"):
        load_dataset(A.replace(b",101\n", b",1.5\n", 1), B)
    with pytest.raises(DataQualityError, match="DATE"):
        load_dataset(A.replace(b"2026-08-01", b"2026-13-01", 1), B)
    with pytest.raises(DataQualityError, match="SIZE"):
        load_dataset(b"x" * (2 * 1024 * 1024 + 1), B)
    with pytest.raises(DataQualityError, match="EMPTY"):
        load_dataset(b"date,channel,sessions\n", B)


def test_r02_duplicate_traffic_row():
    lines = A.splitlines()
    with pytest.raises(DataQualityError, match="DUP_TRAFFIC"):
        load_dataset(b"\n".join(lines + [lines[1]]) + b"\n", B)


def test_r02_identical_order_line_and_crossline_conflict():
    lines = B.splitlines()
    with pytest.raises(DataQualityError, match="DUP_LINE"):
        load_dataset(A, b"\n".join(lines + [lines[1]]) + b"\n")
    changed = rewrite_csv(B, ["order_id", "date", "channel", "product", "quantity", "revenue"],
                          lambda data: data[1].update({"date": "2026-08-21"}))
    with pytest.raises(DataQualityError, match="ORDER_CONFLICT"):
        load_dataset(A, changed)


def test_r02_unknown_channel_and_bad_quantity():
    altered = rewrite_csv(B, ["order_id", "date", "channel", "product", "quantity", "revenue"],
                          lambda rows: [item.update({"channel": "unknown"}) for item in rows if item["channel"] == "広告"])
    with pytest.raises(DataQualityError, match="UNKNOWN_CHANNEL"):
        load_dataset(A, altered)
    with pytest.raises(DataQualityError, match="RANGE"):
        load_dataset(A, B.replace(b",1,", b",0,", 1))


def test_r03_distinct_order_count_in_multiple_products():
    r = report()
    # Two product lines per order, but one distinct order globally.
    assert len(r.context.dataset.lines) == 96
    assert r.sections["S01_KPI_SUMMARY"]["2026-08"]["orders"] == 24
    assert r.sections["S04_PRODUCT"]["rows"][0]["baseline"]["orders_with_product"] == 24
    assert sum(p["target"]["orders_with_product"] for p in r.sections["S04_PRODUCT"]["rows"]) == 48
    assert r.sections["S01_KPI_SUMMARY"]["2026-09"]["orders"] == 24


def test_r04_contiguous_month_and_reverse_and_gap():
    assert prior_month("2026-01") == "2025-12"
    assert_contiguous("2026-08", "2026-09")
    with pytest.raises(DataQualityError, match="NOT_PREVIOUS_MONTH"):
        assert_contiguous("2026-08", "2026-10")
    with pytest.raises(DataQualityError, match="NOT_PREVIOUS_MONTH"):
        assert_contiguous("2026-10", "2026-08")
    assert report().context.comparable is True


def test_r04_r05_r07_fixture_full_seven_analyses_and_reconciliation():
    r = report()
    assert list(r.sections) == [
        "S01_KPI_SUMMARY", "S02_PERIOD_COMPARISON", "S03_CHANNEL", "S04_PRODUCT",
        "S05_GROWTH_DECLINE", "S06_REVENUE_BRIDGE", "S07_DAILY_CHANGE"
    ]
    assert r.sections["S01_KPI_SUMMARY"]["2026-08"]["revenue"] == 800_000
    assert r.sections["S01_KPI_SUMMARY"]["2026-09"]["revenue"] == 670_000
    assert r.sections["S02_PERIOD_COMPARISON"]["metrics"]["revenue"]["difference"] == -130_000
    bridge = r.sections["S06_REVENUE_BRIDGE"]
    assert bridge["overall_delta"] == -130_000
    assert sorted(bridge["by_channel"].values()) == [-150_000, 20_000]
    assert bridge["channel_reconciled"] and bridge["product_reconciled"]
    assert sum(bridge["by_product"].values()) == -130_000
    assert len(r.sections["S07_DAILY_CHANGE"]) == 29


def test_r04_zero_denominator_is_unknown_not_cvr():
    changed = rewrite_csv(A, ["date", "channel", "sessions"],
                          lambda rows: [row.update({"sessions": "0"}) for row in rows])
    r = report(changed)
    assert r.sections["S01_KPI_SUMMARY"]["2026-09"]["orders_per_session"] is None
    assert r.sections["S02_PERIOD_COMPARISON"]["metrics"]["orders_per_session"]["difference"] is None
    assert "conversion" not in str(r.sections).lower()


def test_r08_missing_one_day_in_one_month_does_not_zero_fill():
    lines = A.decode("utf-8").splitlines(keepends=True)
    changed = "".join(l for l in lines if not (l.startswith("2026-08-31") and ",検索," in l)).encode("utf-8")
    r = report(changed)
    assert not r.context.comparable
    assert "2026-08-31" in r.context.missing_days
    assert r.sections["S02_PERIOD_COMPARISON"]["metrics"] == {}
    assert r.sections["S06_REVENUE_BRIDGE"]["comparable"] is False
    assert r.sections["S01_KPI_SUMMARY"]["2026-08"]["sessions"] < report().sections["S01_KPI_SUMMARY"]["2026-08"]["sessions"]
    assert r.facts == {}


def test_r06_max_three_or_zero_no_duplicate_and_order_stable():
    a = report()
    b = report()
    assert len(a.important_insights) <= 3
    assert len({x["metric"] for x in a.important_insights}) == len(a.important_insights)
    assert [x["fact_id"] for x in a.important_insights] == [x["fact_id"] for x in b.important_insights]
    assert all("独立した原因" not in i["text"] for i in a.important_insights)


def test_r06_r09_all_metrics_flat_has_no_change_and_safe_uncertainty():
    # Repeat Aug observations into Sep while preserving Sep's 30-day structural completeness.
    from kpi_investigator.analysis import _rows_for_month
    # Use a minimal fully covered two-month fixture with equal metrics and no 31st day mismatch.
    a = b"date,channel,sessions\n" + b"".join(
        f"2026-{month:02d}-{day:02d},A,1\n".encode()
        for month, days in ((8,31),(9,30)) for day in range(1,days+1)
    )
    b = (b"order_id,date,channel,product,quantity,revenue\n"
         b"AUG,2026-08-01,A,product,1,1000\nSEP,2026-09-01,A,product,1,1000\n")
    r = report(a,b)
    assert r.context.comparable
    assert r.sections["S02_PERIOD_COMPARISON"]["metrics"]["revenue"]["difference"] == 0
    assert r.sections["S02_PERIOD_COMPARISON"]["metrics"]["orders"]["difference"] == 0
    # Sessions have different month lengths; normalize total: Aug31 sessions, Sep31 sessions.
    a2 = a.replace(b"2026-09-01,A,1\n", b"2026-09-01,A,2\n")
    r = report(a2,b)
    assert all(r.sections["S02_PERIOD_COMPARISON"]["metrics"][key]["difference"] == 0 for key in ("revenue","orders","sessions","quantity","average_order_value","orders_per_session"))
    assert r.important_insights == []
    value = validated_interpretation(r, {"interpretation_type":"INSUFFICIENT_TO_IDENTIFY_CAUSE","fact_ids":[],"uncertainty_type":"CAUSE_UNKNOWN"})
    assert "変化は確認" not in value and "原因・要因" in value
    assert validated_interpretation(r, demo_candidate("OVERALL_REVENUE_DOWN", r)) is None


def test_r09_equal_channel_differences_do_not_claim_variation():
    a = b"date,channel,sessions\n" + b"".join(
        f"2026-{month:02d}-{d:02d},{channel},1\n".encode()
        for month, days in ((8,31),(9,30)) for d in range(1,days+1) for channel in ("A","B")
    )
    b = (b"order_id,date,channel,product,quantity,revenue\n"
         b"AUGA,2026-08-01,A,X,1,20000\nAUGB,2026-08-01,B,X,1,20000\n"
         b"SEPA,2026-09-01,A,X,1,30000\nSEPB,2026-09-01,B,X,1,30000\n")
    r = report(a,b)
    assert [p["revenue_delta"] for p in r.sections["S03_CHANNEL"]["rows"]] == [10000,10000]
    answer = validated_interpretation(r,demo_candidate("CHANNEL_VARIATION_OBSERVED",r))
    assert answer and "チャネル別の売上差額を集計" in answer and "チャネル間に差" not in answer


def test_r09_r15_refuses_invalid_codes_ids_extra_text_causality():
    r = report()
    assert validated_interpretation(r, demo_candidate("OVERALL_REVENUE_DOWN", r))
    assert validated_interpretation(r, demo_candidate("MULTIPLE_METRICS_DOWN", r))
    assert validated_interpretation(r, demo_candidate("CHANNEL_VARIATION_OBSERVED", r))
    assert validated_interpretation(r, demo_candidate("INSUFFICIENT_TO_IDENTIFY_CAUSE", r))
    for change in (
        {"interpretation_type":"PROFIT_MARGIN_IMPROVED"},
        {"interpretation_type":"SEO_CAUSED_LOSS"},
        {"interpretation_type":"CHANNEL_VARIATION_OBSERVED", "fact_ids":["OVERALL_REVENUE_DELTA"]},
        {"fact_ids":["MADE_UP_ID"]},
        {"fact_ids":[]},
        {"uncertainty_type":"OTHER"},
        {"free_text":"SEO悪化が原因です"},
        {"cause":"profit margin improved"},
        {"fact_ids":["OVERALL_REVENUE_DELTA", "OVERALL_REVENUE_DELTA"]},
    ):
        candidate=demo_candidate("OVERALL_REVENUE_DOWN",r);candidate.update(change)
        assert validated_interpretation(r,candidate) is None,change


def test_r17_pregenerated_verified_rejects_old_text_same_pipeline():
    r = report()
    manifest = load_pregenerated()
    assert manifest and manifest["generation_origin"] == "approved-deterministic-selection/no-api"
    text = verified_pregenerated(r,manifest,traffic_raw=A,lines_raw=B)
    assert text and len(text)==2 and "チャネル間に差" not in "".join(text)
    old = copy.deepcopy(manifest)
    old["interpretations"][1]["template_text"]="チャネル間に差があります。"
    assert verified_pregenerated(r,old,traffic_raw=A,lines_raw=B) is None
    wrong_sha = copy.deepcopy(manifest)
    wrong_sha["demo_sha256"] = "x" * 64
    assert verified_pregenerated(r,wrong_sha,traffic_raw=A,lines_raw=B) is None
    assert verified_pregenerated(report(demo=False),manifest,traffic_raw=A,lines_raw=B) is None


def test_r17_pregenerated_all_zero_old_false_change_rejected():
    # Actual full-calendar dataset with every tracked metric flat: not merely a text replacement.
    a = b"date,channel,sessions\n" + b"".join(
        f"2026-{month:02d}-{day:02d},A,{2 if month == 9 and day == 1 else 1}\n".encode()
        for month, days in ((8,31),(9,30)) for day in range(1,days+1)
    )
    b = (b"order_id,date,channel,product,quantity,revenue\n"
         b"AUG,2026-08-01,A,product,1,1000\nSEP,2026-09-01,A,product,1,1000\n")
    r = report(a,b)
    assert all(change["difference"] == 0 for change in r.sections["S02_PERIOD_COMPARISON"]["metrics"].values())
    m = copy.deepcopy(load_pregenerated())
    m["demo_sha256"] = demo_fingerprint(a,b)
    m["interpretations"] = [{
        "selection": {"interpretation_type":"INSUFFICIENT_TO_IDENTIFY_CAUSE", "fact_ids":[],
                      "uncertainty_type":"CAUSE_UNKNOWN"},
        "template_text":"原因・要因については、このデータだけでは判断できません。 原因は特定できません。",
    }]
    assert verified_pregenerated(r,m,traffic_raw=a,lines_raw=b)
    m["interpretations"][0]["template_text"] = "データで変化は確認できます。"
    assert verified_pregenerated(r,m,traffic_raw=a,lines_raw=b) is None


def test_r17_pregenerated_equal_channel_delta_old_false_variation_rejected():
    a = b"date,channel,sessions\n" + b"".join(
        f"2026-{month:02d}-{day:02d},{channel},1\n".encode()
        for month, days in ((8,31),(9,30)) for day in range(1,days+1) for channel in ("A","B")
    )
    b = (b"order_id,date,channel,product,quantity,revenue\n"
         b"AUGA,2026-08-01,A,X,1,20000\nAUGB,2026-08-01,B,X,1,20000\n"
         b"SEPA,2026-09-01,A,X,1,30000\nSEPB,2026-09-01,B,X,1,30000\n")
    r=report(a,b)
    m=copy.deepcopy(load_pregenerated())
    m["demo_sha256"]=demo_fingerprint(a,b)
    m["interpretations"]=[{
        "selection":{"interpretation_type":"CHANNEL_VARIATION_OBSERVED",
                      "fact_ids": sorted(x for x in r.facts if x.startswith("CHANNEL_REVENUE_DELTA_")),
                      "uncertainty_type":"LIMITED_DATA"},
        "template_text":"チャネル別の売上差額を集計しました。内訳を確認してください。 今回の入力範囲を超えることは判断できません。",
    }]
    assert verified_pregenerated(r,m,traffic_raw=a,lines_raw=b)
    m["interpretations"][0]["template_text"]="チャネル間に差があります。"
    assert verified_pregenerated(r,m,traffic_raw=a,lines_raw=b) is None


def test_r03_three_products_in_one_order_still_one_order():
    a=b"date,channel,sessions\n" + b"".join(
        f"2026-{month:02d}-{day:02d},A,1\n".encode()
        for month, days in ((8,31),(9,30)) for day in range(1,days+1)
    )
    b=(b"order_id,date,channel,product,quantity,revenue\n" + b"".join(
        f"{oid},{day},A,{product},1,1000\n".encode()
        for oid, day in (("AUG","2026-08-01"),("SEP","2026-09-01"))
        for product in ("X","Y","Z")
    ))
    r=report(a,b)
    assert r.sections["S01_KPI_SUMMARY"]["2026-09"]["orders"]==1
    assert r.sections["S01_KPI_SUMMARY"]["2026-09"]["revenue"]==3000
    assert sum(x["target"]["orders_with_product"] for x in r.sections["S04_PRODUCT"]["rows"])==3


def test_r10_minimized_payload_and_default_no_send():
    r = report(demo=False)
    p = build_api_payload(r)
    preview=canonical_json(p)
    assert validate_api_payload(r,p)
    assert p["store"] is False and p["background"] is False
    assert p["model"] == "gpt-6.1-sol"
    assert p["max_output_tokens"] == 3000
    for forbidden in ("SYN-", "検索", "広告", "定番セット", "季節セット", "670000", "800000", "order_id", "product", "channel"):
        assert forbidden not in preview
    assert "最大30日" in CONSUMER_DISCLOSURE and "さらに保持" in CONSUMER_DISCLOSURE
    outcome, trans, quota = scenario_ai(r,a=A,b=B,consent=None)
    assert outcome == (None,"NO_CONSENT") and trans.calls == [] and quota.counts("browser-1") == (0,0)


def test_m3_store_false_background_false_malformed_payload_refused():
    r = report()
    p=build_api_payload(r)
    for change in ({"store":True},{"background":True},{"model":"other"},{"raw_order_id":"hidden"}):
        q=copy.deepcopy(p);q.update(change)
        assert validate_api_payload(r,q) is False


def test_m4_consent_binds_both_csv_bytes_month_and_final_payload():
    r=report(demo=False)
    p=build_api_payload(r)
    consent=bind_consent(A,B,"2026-09",p,accepted=True)
    assert valid_consent(consent,A,B,"2026-09",p)
    assert not valid_consent(consent,A,B+b"\n","2026-09",p)  # same file name, changed bytes
    assert not valid_consent(consent,A+b"\n",B,"2026-09",p)
    assert not valid_consent(consent,A,B,"2026-08",p)
    changed=copy.deepcopy(p);changed["max_output_tokens"]=2999
    assert not valid_consent(consent,A,B,"2026-09",changed)
    denied, transport, quota=scenario_ai(r,a=A,b=B,consent=consent, request="mock-ok")
    assert denied[1]=="MOCK_VALIDATED" and len(transport.calls)==1
    assert canonical_json(transport.calls[0])==canonical_json(p)
    denied,transport,quota=scenario_ai(r,a=A,b=B+b"\n",consent=consent,request="new")
    assert denied==(None,"NO_CONSENT") and not transport.calls


def test_r10_insufficient_orders_and_differencing_risk_refused():
    # Valid two-month set with one order each; security rejects despite user consent.
    mask=b"order_id,date,channel,product,quantity,revenue\n"
    mask+=b"ONE,2026-08-01,\xe6\xa4\x9c\xe7\xb4\xa2,\xe5\xae\x9a\xe7\x95\xaa\xe3\x82\xbb\xe3\x83\x83\xe3\x83\x88,1,100\n"
    mask+=b"TWO,2026-09-01,\xe6\xa4\x9c\xe7\xb4\xa2,\xe5\xae\x9a\xe7\x95\xaa\xe3\x82\xbb\xe3\x83\x83\xe3\x83\x88,1,100\n"
    with pytest.raises(SafeSendRejected):build_api_payload(report(A,mask,demo=False))
    # 12 orders per month but each other subgroup with only 9 => reject.
    names=["order_id","date","channel","product","quantity","revenue"]
    def thin(rows):
        for row in rows:
            if row["product"] == "定番セット" and row["order_id"].endswith(("01","02","03")):
                row["product"]="小集計"
    modified=rewrite_csv(B,names,thin)
    with pytest.raises(SafeSendRejected):build_api_payload(report(A,modified,demo=False))


def test_r11_r14_no_retry_duplicate_and_post_reservation_timeout_consumes():
    r=report()
    state=InMemorySharedState();quota=MockAtomicQuota(state)
    result, t, quota=scenario_ai(r,quota=quota,failure=True,request="unknown-1")
    assert result==(None,"MOCK_FAILED") and len(t.calls)==1
    assert quota.counts("browser-1")== (1,1)
    second,t2,_=scenario_ai(r,quota=MockAtomicQuota(state),request="unknown-1")
    assert second==(None,"DUPLICATE") and len(t2.calls)==0
    denied,t3,q3=scenario_ai(r,allow=False)
    assert denied==(None,"LIVE_OFF") and len(t3.calls)==0 and q3.counts("browser-1")== (0,0)


def test_r13_parallel_twenty_one_remaining_slot_and_restart():
    state=InMemorySharedState();quota=MockAtomicQuota(state)
    for i in range(9):
        assert quota.reserve(f"b{i//3}",f"seed-{i}")=="RESERVED"
    barrier=threading.Barrier(20)
    def one(i):
        barrier.wait()
        return MockAtomicQuota(state).reserve(f"fresh-{i}",f"req-{i}")
    with ThreadPoolExecutor(max_workers=20) as pool:
        results=list(pool.map(one,range(20)))
    assert results.count("RESERVED")==1
    assert results.count("GLOBAL_LIMIT")==19
    assert quota.counts("b0")[1]==10
    assert MockAtomicQuota(state).reserve("fresh-99","seed-0")=="DUPLICATE"


def test_r13_jst_midnight_day_partition_and_dedup():
    state=InMemorySharedState();q=MockAtomicQuota(state)
    previous=datetime(2026,10,12,23,59,59,tzinfo=JST)
    next_day=datetime(2026,10,13,0,0,1,tzinfo=JST)
    assert day_key(previous)=="2026-10-12" and day_key(next_day)=="2026-10-13"
    assert seconds_until_next_jst_midnight(previous) <= 2
    assert q.reserve("b","cross-day-id",now=previous)=="RESERVED"
    assert q.reserve("b","cross-day-id",now=next_day)=="DUPLICATE"
    assert q.reserve("b","different",now=next_day)=="RESERVED"
    assert q.counts("b",now=next_day)==(1,1)


def test_r13_redis_adapter_contract_no_real_connection_and_errors():
    calls=[]
    def fake(lua,keys,args):
        calls.append((lua,keys,args))
        return 1
    q=RedisQuotaContract(fake)
    assert q.reserve("anon", "request", now=datetime(2026,10,12,12,tzinfo=JST))=="RESERVED"
    assert len(calls)==1 and len(calls[0][1])==3
    assert "EVAL" in calls[0][0] and "EXISTS" in calls[0][0]
    assert all(not k.endswith(":anon") and not k.endswith(":request") for k in calls[0][1])
    assert calls[0][2][:2]==[3,10]
    def no_conn(*args):raise ConnectionError("SENSITIVE_ENDPOINT")
    assert RedisQuotaContract(no_conn).reserve("a","r")=="REDIS_ERROR"


def test_r14_redis_outage_zero_sent_even_with_verified_mock_budget():
    r=report()
    result,t,q=scenario_ai(r,quota=MockAtomicQuota(offline=True))
    assert result==(None,"MOCK_FAILED") and t.calls==[]
    result,t,q=scenario_ai(r,quota=RedisQuotaContract(lambda *x: (_ for _ in ()).throw(ConnectionError("down"))))
    assert result==(None,"REDIS_ERROR") and t.calls==[]


def test_r16_m1_m2_unverified_budget_fails_closed():
    r=report()
    conditions=(BudgetAttestation(), BudgetAttestation(True,False,True,15,True),
                BudgetAttestation(True,True,False,15,True),BudgetAttestation(True,True,True,15,False),
                BudgetAttestation(False,True,True,15,True),BudgetAttestation(True,True,True,14,True))
    for att in conditions:
        result,t,q=scenario_ai(r,budget=att)
        assert result==(None,"LIVE_OFF") and t.calls==[]
    assert "Admin" not in (ROOT/"app.py").read_text("utf-8")


def test_r12_true_trace_exact_source_manifest_source_mismatch():
    r=report()
    assert len(r.events)==8
    for event in r.events:
        assert event.function_id in SOURCE_ALLOWLIST
        assert event.elapsed_ms>=0
        code=inspect_trace(event)
        assert code and code.lstrip().startswith("def ")
        assert verify_source_manifest(event.function_id)==code
    assert verify_source_manifest("../../.streamlit/secrets.toml") is None
    wrong=TraceEvent(r.events[0].function_id, "tampered", 0, "0" * 64)
    assert inspect_trace(wrong) is None
    html=render_trace_html(r.events)
    assert "1x 再生" in html and "2x 再生" in html and "Skip" in html
    assert "処理済みの記録を再生" in html
    assert "__TRACE_JSON__" not in html
    assert "S07_DAILY_CHANGE" in html
    assert "SYN-" not in html and "検索" not in html


def test_m5_no_exception_stack_secrets_or_raw_cell_values():
    public_token="SUPERPRIVATE_ABC_DEF"
    corrupted=B.replace("定番セット".encode(),public_token.encode(),1)
    corrupted=corrupted.replace(b",1,", b",broken_value,", 1)
    with pytest.raises(DataQualityError) as err:
        load_dataset(A,corrupted)
    assert public_token not in err.value.public_message and "broken_value" not in err.value.public_message
    fail,t,q=scenario_ai(report(),failure=True)
    assert fail==(None,"MOCK_FAILED") and "INTERNAL_SECRET_MUST_NOT_BE_DISPLAYED" not in str(fail)
    conf=(ROOT/".streamlit/config.toml").read_text("utf-8")
    assert re.search(r'showErrorDetails\s*=\s*"none"',conf)
    assert re.search(r'maxUploadSize\s*=\s*2',conf)


def test_r01_r18_project_exposes_single_report_button_and_no_questions():
    src=(ROOT/"app.py").read_text("utf-8")
    assert src.count('st.button("総合レポートを生成"')==1
    assert "st.chat_input(" not in src
    assert "今回実行した関数のソースコードを開く" in src
    assert "https://github.com/hiromi1992/kpi-investigator" in src
    assert "Webアプリは未公開" in src
    assert "未作成・未公開" not in src
    assert "CSVはこのアプリの実行サーバーへ送信され" in src
    assert "OpenAIなどの外部AIサービスには送信しません" in src
    assert "st.tabs(" in src


def test_packaged_source_provenance_and_no_external_credentials():
    assert "gpt-6.1-sol" in (ROOT/"kpi_investigator/ai.py").read_text("utf-8")
    assert not (ROOT/".streamlit/secrets.toml").exists()
    ignored=(ROOT/".gitignore").read_text("utf-8")
    assert ".env" in ignored and ".streamlit/secrets.toml" in ignored
    # A real GitHub Actions checkout naturally has a .git directory.
    assert "upstash_redis" not in (ROOT/"app.py").read_text("utf-8")
    assert "import openai" not in (ROOT/"app.py").read_text("utf-8").lower()
