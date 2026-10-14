"""Local-only deterministic bundle generation; no API / Redis / Git operations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kpi_investigator import ANALYSIS_VERSION, TEMPLATE_VERSION
from kpi_investigator.analysis import build_report
from kpi_investigator.interpretation import demo_fingerprint, validated_interpretation
from kpi_investigator.trace import SOURCE_ALLOWLIST, _get_actual_source

ROOT = Path(__file__).resolve().parents[1]


def build():
    traffic = (ROOT / "fixtures" / "traffic.csv").read_bytes()
    orders = (ROOT / "fixtures" / "order_lines.csv").read_bytes()
    report = build_report(traffic, orders, target="2026-09", is_demo=True)
    selections = [
        {"interpretation_type": "OVERALL_REVENUE_DOWN",
         "fact_ids": ["OVERALL_REVENUE_DELTA"], "uncertainty_type": "CAUSE_UNKNOWN"},
        {"interpretation_type": "CHANNEL_VARIATION_OBSERVED",
         "fact_ids": sorted(k for k in report.facts if k.startswith("CHANNEL_REVENUE_DELTA_")),
         "uncertainty_type": "LIMITED_DATA"},
    ]
    pregenerated = {
        "schema_version": "pregenerated/v1", "analysis_version": ANALYSIS_VERSION,
        "template_version": TEMPLATE_VERSION, "target_month": "2026-09",
        "demo_sha256": demo_fingerprint(traffic, orders),
        # Deterministic and approved rule selection; no API, do not masquerade as an AI run.
        "generation_origin": "approved-deterministic-selection/no-api",
        "generator_model_id": "NO_MODEL_DETERMINISTIC_RULES",
        "source_sha256": {name: hashlib.sha256((ROOT / "kpi_investigator" / name).read_bytes()).hexdigest()
                          for name in ("analysis.py", "data.py", "interpretation.py")},
        "interpretations": [
            {"selection": s, "template_text": validated_interpretation(report, s)} for s in selections
        ],
    }
    assert all(i["template_text"] for i in pregenerated["interpretations"])
    (ROOT / "fixtures" / "pregenerated.json").write_text(
        json.dumps(pregenerated, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {"analysis_version": ANALYSIS_VERSION,
                "functions": {name: hashlib.sha256(_get_actual_source(name).encode("utf-8")).hexdigest()
                              for name in sorted(SOURCE_ALLOWLIST)}}
    (ROOT / "code_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print("Pinned functions:", len(manifest["functions"]))
    print("Prebuilt deterministic interpretations:", len(pregenerated["interpretations"]))


if __name__ == "__main__":
    build()
