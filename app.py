"""KPI Investigator Streamlit dashboard: offline mode only; no AI/Redis connectors."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from kpi_investigator import ANALYSIS_VERSION
from kpi_investigator.ai import SafeSendRejected, build_api_payload
from kpi_investigator.analysis import METRIC_LABELS, METRIC_UNITS, Report, build_report
from kpi_investigator.consent import CONSUMER_DISCLOSURE, bind_consent, canonical_json, content_hash
from kpi_investigator.data import DataQualityError, available_months, load_dataset
from kpi_investigator.interpretation import load_pregenerated, verified_pregenerated
from kpi_investigator.player import render_trace_html
from kpi_investigator.trace import inspect_trace

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="KPI Investigator · Python Analytics", page_icon="📊", layout="wide")
st.markdown("""<style>
.block-container{max-width:1260px;padding-top:2rem;padding-bottom:5rem}
[data-testid="stMetric"]{padding:16px 20px;border:1px solid #dae5e9;border-radius:14px;background:#fff;box-shadow:0 4px 22px #142d3b08}
[data-testid="stMetricLabel"]{font-size:.86rem}
.stButton>button[kind="primary"]{background:#087f67;border:none;border-radius:10px;font-weight:650}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:12px}
h1,h2,h3{letter-spacing:-.02em}
</style>""", unsafe_allow_html=True)


def format_metric(name: str, value: int | float | None) -> str:
    if value is None:
        return "算出不可"
    if name == "orders_per_session":
        return f"{value:.3f} 注文/セッション"
    if name == "average_order_value":
        return f"¥{value:,.0f}"
    return f"¥{value:,.0f}" if name == "revenue" else f"{value:,.0f} {METRIC_UNITS[name]}"


def display_report(report: Report, current_raw: tuple[bytes, bytes] | None):
    ctx = report.context
    metrics = report.sections["S01_KPI_SUMMARY"][ctx.target]
    changes = report.sections["S02_PERIOD_COMPARISON"]["metrics"]
    st.markdown(f"### {ctx.target} · 総合レポート")
    if ctx.comparable:
        st.caption(f"比較基準：{ctx.baseline}（直前の暦月） · 両月の観測チャネル×日付を検証")
    else:
        st.warning("前月比較不可：日付または観測チャネルの行が不足しています。欠測を0扱いせず、観測された行の合計のみ表示しています。")
        st.caption("不足日付数：" + str(len(ctx.missing_days)) + "日。アップロードした両CSVの対象月・直前月を確認してください。")
    st.caption("CSVに記録されないチャネルが存在するか、実際の注文がすべて記録されたかは判定できません。")

    cols = st.columns(3)
    for idx, name in enumerate(("revenue", "orders", "sessions", "quantity", "average_order_value", "orders_per_session")):
        val = metrics[name]
        delta = changes.get(name, {}).get("difference") if ctx.comparable else None
        if delta is not None and name != "orders_per_session":
            diff_text = (f"{delta:+,.0f}円" if name in ("revenue", "average_order_value") else f"{delta:+,.0f} {METRIC_UNITS[name]}")
        elif delta is not None:
            diff_text = f"{delta:+.3f}"
        else:
            diff_text = None
        with cols[idx % 3]:
            st.metric(METRIC_LABELS[name], format_metric(name, val), delta=diff_text, delta_color="off")

    st.markdown("### 重要所見")
    if report.important_insights:
        for item in report.important_insights:
            st.info(item["text"] + "　（Python確認済み／" + item["view"] + "）")
    else:
        st.info("重要所見として表示する非ゼロの変化はありません。変化があったとは断定しません。")

    if ctx.dataset.is_demo and current_raw:
        manifest = load_pregenerated()
        explanations = verified_pregenerated(report, manifest or {}, traffic_raw=current_raw[0], lines_raw=current_raw[1])
        if explanations:
            with st.container(border=True):
                st.markdown("**事前生成済みの説明（ライブAIではありません）**")
                st.caption("承認済みのルールで選んだ固定コードを今回のPython計算と照合。生成時に実AIを使ったとは主張しません。")
                for text in explanations:
                    st.write(text)
        else:
            st.info("固定説明のハッシュまたは根拠を検証できないため、Pythonの計算結果だけを表示しています。")
    else:
        with st.container(border=True):
            st.markdown("**利用者CSVのAI送信設定**")
            st.caption("既定：送信しない。ローカルMVPではAI通信機能自体が無効です。")
            if ctx.comparable:
                try:
                    payload = build_api_payload(report)
                    st.caption("送信先：OpenAI Responses API · 送信前に確認する最終JSON（プレビューとモック送信本文が同一）")
                    st.code(canonical_json(payload), language="json")
                    st.info(CONSUMER_DISCLOSURE)
                    if current_raw is not None:
                        # Changing either file's bytes, target month or payload creates a new default-OFF widget.
                        binding_identity = content_hash((content_hash(current_raw[0]) + content_hash(current_raw[1]) + ctx.target + canonical_json(payload)).encode())
                        accepted = st.checkbox("このプレビューの情報のAI送信に同意する（現MVPでは送信しません）", value=False, key="consent_" + binding_identity)
                        if accepted:
                            _binding = bind_consent(*current_raw, ctx.target, payload, accepted=True)
                            st.success("この内容への同意が選択されました。現在は外部送信を実行しません。")
                except SafeSendRejected:
                    st.warning("少数集計、再識別リスクまたは不十分なデータ品質のため、AI送信を許可できません。")
            st.caption("実APIの設定・予算上限・同意およびレート制限は本番公開前に別途検証します。")

    st.markdown("### 計算の内訳")
    ch_tab, prod_tab, rank_tab, bridge_tab, daily_tab = st.tabs(["チャネル", "商品", "増減ランキング", "売上の検算", "日次変化"])
    with ch_tab:
        import pandas as pd
        rows = report.sections["S03_CHANNEL"]["rows"]
        st.dataframe(pd.DataFrame([{"チャネル":r["channel"], "対象月 売上(円)":r["target"]["revenue"],
             "基準月 売上(円)":r["baseline"]["revenue"], "前月差額(円)":r["revenue_delta"],
             "対象月 セッション":r["target"]["sessions"], "対象月 注文数":r["target"]["orders"]} for r in rows]),hide_index=True,use_container_width=True)
        st.caption("注文数はチャネル内でユニーク注文IDを数えています。SEOや広告の因果効果は判断できません。")
    with prod_tab:
        import pandas as pd
        rows = report.sections["S04_PRODUCT"]["rows"]
        st.dataframe(pd.DataFrame([{"商品":r["product"], "対象月 売上(円)":r["target"]["revenue"],
            "前月差額(円)":r["revenue_delta"], "数量":r["target"]["quantity"],
            "商品を含む注文数":r["target"]["orders_with_product"]} for r in rows]),hide_index=True,use_container_width=True)
        st.caption("1注文に複数の商品が含まれる場合、商品別注文数の合計は全注文数と一致しません。")
    with rank_tab:
        data = report.sections["S05_GROWTH_DECLINE"]
        st.markdown("**同じ単位（売上差額・円）で各集計軸内のみ並べ替え**")
        for name, dimension in (("チャネル", "channels"), ("商品", "products")):
            st.markdown("**"+name+"**")
            for key, caption in (("growth", "増加"), ("decline", "減少")):
                rows = data[dimension][key]
                st.write(caption + "：" + (" / ".join(f"{(r.get('channel') or r.get('product'))}: {r['delta_yen']:+,}円" for r in rows[:5]) if rows else "なし"))
    with bridge_tab:
        b = report.sections["S06_REVENUE_BRIDGE"]
        if b["comparable"]:
            st.markdown(f"**全体の売上差：{b['overall_delta']:+,}円**")
            st.write("チャネル別内訳：", {k: f"{v:+,}円" for k,v in b["by_channel"].items()})
            st.write("商品別内訳：", {k: f"{v:+,}円" for k,v in b["by_product"].items()})
            st.write("照合：", "両方とも一致" if b["channel_reconciled"] and b["product_reconciled"] else "不整合")
            st.caption(b["note"])
        else:
            st.warning("比較月不完全のため差額分解は表示しません。")
    with daily_tab:
        daily = report.sections["S07_DAILY_CHANGE"]
        if daily:
            import pandas as pd
            st.dataframe(pd.DataFrame(daily).rename(columns={"date":"日付", "revenue":"対象日売上(円)",
                   "previous_revenue":"前日売上(円)", "difference":"前日差額(円)"}),hide_index=True,use_container_width=True)
            st.caption("入力で観測できた連続日の変化です。統計上の異常とは断定しません。")
        else:
            st.caption("比較できる連続日はありません。")

    st.markdown("### 根拠と不確実性")
    a,b,c,d=st.columns(4)
    a.success("**確認できた事実**\n\nPython集計結果・根拠ID")
    b.info("**解釈・仮説**\n\n検証済みの固定文のみ")
    c.caption("**外部調査**\n\n未実施")
    d.warning("**現状では不明**\n\n因果、未記載チャネル、実態の網羅性")
    with st.expander("Pythonの根拠IDを確認"):
        for identifier, item in report.facts.items():
            st.write("`" + identifier + "` · " + item["text"])

    st.markdown("### 実際の処理を再生")
    st.caption("処理はすでに完了しています。下の演出は計算にかかった実測時間を使った再生で、再実行や追加AI呼出ではありません。")
    components.html(render_trace_html(report.events), height=276, scrolling=False)
    with st.expander("今回実行した関数のソースコードを開く（再課金なし）"):
        for event in report.events:
            source = inspect_trace(event)
            with st.expander(event.function_id + " · 実測 " + f"{event.elapsed_ms:.3f} ms"):
                if source:
                    st.code(source, language="python")
                    st.caption("実行関数と固定SHA-256で照合した公開許可コードのみ表示")
                else:
                    st.warning("ソースコードの版を照合できないため表示しません。")


st.markdown("# KPI Investigator")
st.markdown("**Pythonで数字を検算し、根拠をたどれるEC分析ダッシュボード**")
st.caption("v" + ANALYSIS_VERSION + " · 外部AI/Redis未接続のPython分析デモ · 予測や因果断定は行いません")
with st.container(border=True):
    source = st.radio("データ", ["合成デモ（推奨）", "自分のCSV 2ファイル"], horizontal=True)
    demo = source.startswith("合成")
    raw: tuple[bytes, bytes] | None = None
    if demo:
        raw = ((ROOT / "fixtures" / "traffic.csv").read_bytes(),
               (ROOT / "fixtures" / "order_lines.csv").read_bytes())
        st.caption("完全な合成データ／2026年8月と9月。初期画面から1操作でレポートを生成できます。")
    else:
        st.warning("アップロードしたCSVはこのアプリの実行サーバーへ送信され、Python分析に使用されます。OpenAIなどの外部AIサービスには送信しません。個人情報や秘密情報が入ったCSVはアップロードしないでください。")
        l,r = st.columns(2)
        with l: file_a = st.file_uploader("traffic.csv", type="csv", key="traffic", max_upload_size=2)
        with r: file_b = st.file_uploader("order_lines.csv", type="csv", key="orders", max_upload_size=2)
        if file_a is not None and file_b is not None:
            raw = (file_a.getvalue(), file_b.getvalue())
    options = []
    if raw:
        try:
            options = available_months(load_dataset(*raw, is_demo=demo))
        except DataQualityError as err:
            st.error(err.public_message)
        except Exception:
            st.error("CSVを読み込めませんでした。ファイル形式を確認してください。")
    target = st.selectbox("対象月（基準月は自動で直前の暦月に固定）", options, index=len(options)-1 if options else None,
                          placeholder="CSVの検査後に選択できます", disabled=not bool(options))
    clicked = st.button("総合レポートを生成", type="primary", disabled=not bool(raw and target), use_container_width=True)

# Do not keep showing a previously generated result when the file content or month changes.
source_key = (demo, target, content_hash(raw[0]), content_hash(raw[1])) if raw and target else None
if st.session_state.get("last_source_key") != source_key:
    st.session_state.pop("last_report", None)
    st.session_state.pop("last_demo_data", None)
    st.session_state.pop("last_source_key", None)

if clicked and raw is not None and target is not None:
    try:
        with st.spinner("Pythonで7つの分析を実行中..."):
            report = build_report(*raw, target=target, is_demo=demo)
        st.session_state["last_report"] = report
        st.session_state["last_source_key"] = source_key
    except DataQualityError as err:
        st.session_state.pop("last_report", None)
        st.error(err.public_message)
    except Exception:
        st.session_state.pop("last_report", None)
        st.error("分析に失敗しました。CSV形式と入力範囲を確認してください。")
if "last_report" in st.session_state:
    display_report(st.session_state["last_report"], raw)

st.divider()
st.caption("公開ソースRepository： https://github.com/hiromi1992/kpi-investigator（Public） | Webアプリは未公開 | 既存の公開ポートフォリオ： https://github.com/hiromi1992/ai-engineering-portfolio")
st.caption("上限：通常ブラウザにつきAI付レポート1日3回、全体10回/日（実接続前の設計値）。現時点ではライブAIを呼べません。")
