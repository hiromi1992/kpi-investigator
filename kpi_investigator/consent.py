"""Consent is bound to actual bytes, target month and the exact displayed payload."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

CONSUMER_DISCLOSURE = (
    "AI解説を使う場合、画面に表示した集計の傾向情報をOpenAI APIへ送信します。"
    "CSVの明細や商品名、正確な売上金額は送信しません。"
    "OpenAIでは通常、不正利用監視のため送信情報を最大30日保持する場合があり、"
    "法令上の必要またはサービス・第三者の保護に合理的に必要な場合は、"
    "さらに保持される可能性があります。"
    "送信しなくてもPythonによる分析レポートは利用できます。"
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class ConsentBinding:
    traffic_sha256: str
    orders_sha256: str
    target_month: str
    api_payload_sha256: str
    accepted: bool


def bind_consent(traffic: bytes, orders: bytes, target: str, payload: dict[str, Any], *, accepted: bool) -> ConsentBinding:
    """Store content IDs, not original file names or uploaded CSV data."""
    return ConsentBinding(content_hash(traffic), content_hash(orders), target,
                          content_hash(canonical_json(payload).encode("utf-8")), accepted)


def valid_consent(binding: ConsentBinding | None, traffic: bytes, orders: bytes,
                  target: str, payload: dict[str, Any]) -> bool:
    if binding is None or not binding.accepted:
        return False
    fresh = bind_consent(traffic, orders, target, payload, accepted=True)
    return binding == fresh
