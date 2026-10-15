"""Build a portable, single-file *offline preview* from the real Python engine."""

from __future__ import annotations

import html
from pathlib import Path

from kpi_investigator.analysis import build_report, METRIC_LABELS
from kpi_investigator.interpretation import load_pregenerated, verified_pregenerated
from kpi_investigator.player import render_trace_html
from kpi_investigator.trace import inspect_trace

ROOT = Path(__file__).resolve().parents[1]


def escape(value) -> str:
    return html.escape(str(value), quote=True)


def build_preview() -> Path:
    a,b = (ROOT / "fixtures/traffic.csv").read_bytes(), (ROOT / "fixtures/order_lines.csv").read_bytes()
    report = build_report(a,b,target="2026-09",is_demo=True)
    assert report.context.comparable
    m = report.sections["S01_KPI_SUMMARY"]["2026-09"]
    comparisons = report.sections["S02_PERIOD_COMPARISON"]["metrics"]
    meta = (
        ("revenue", "¥", ""), ("orders", "", "件"), ("sessions", "", "回"),
        ("quantity", "", "点"), ("average_order_value", "¥", ""), ("orders_per_session", "", "注文/セッション"),
    )
    cards=[]
    for key,prefix,suffix in meta:
        val=m[key]
        formatted = "算出不可" if val is None else f"{val:,.3f}" if key=="orders_per_session" else f"{val:,.0f}"
        delta=comparisons[key]["difference"]
        delta_text="算出不可" if delta is None else f"{delta:+,.3f}" if key=="orders_per_session" else f"{delta:+,.0f}"
        cards.append(f'''<div class="metric"><div class="metric-label">{escape(METRIC_LABELS[key])}</div>
        <div class="metric-value">{escape(prefix)}{escape(formatted)}<span class="unit">{escape(suffix)}</span></div>
        <div class="metric-foot">前月差 <strong class="{'negative' if delta is not None and delta < 0 else 'positive'}">{escape(delta_text)}</strong></div></div>''')
    insights = ''.join(f'<article class="insight"><span class="insight-id">0{i+1}</span><div>{escape(x["text"])}<small>{escape(x["view"])}</small></div></article>' for i,x in enumerate(report.important_insights))
    channel_rows = report.sections["S03_CHANNEL"]["rows"]
    max_change = max(abs(x["revenue_delta"]) for x in channel_rows)
    channels = ''.join('''<div class="bar-row"><div class="bar-name">{}</div><div class="bar-track"><div class="bar-fill {}" style="width:{}%"></div></div><div class="bar-delta {}">{}円</div></div>'''.format(
        escape(x["channel"]),"down" if x["revenue_delta"]<0 else "up", round(abs(x["revenue_delta"])/max_change*100),
        "negative" if x["revenue_delta"]<0 else "positive",f'{x["revenue_delta"]:+,}') for x in channel_rows)
    prodrows=''.join(f'<tr><td>{escape(x["product"])}</td><td>¥{x["target"]["revenue"]:,}</td><td>{x["target"]["orders_with_product"]:,}</td><td class="negative">{x["revenue_delta"]:+,}円</td></tr>' for x in report.sections["S04_PRODUCT"]["rows"])
    changes=report.sections["S07_DAILY_CHANGE"][:8]
    dayrows=''.join(f'<tr><td>{escape(x["date"])}</td><td>{x["revenue"]:,}円</td><td>{x["difference"]:+,}円</td></tr>' for x in changes)
    texts=verified_pregenerated(report,load_pregenerated() or {},traffic_raw=a,lines_raw=b)
    assert texts
    explanations=''.join(f'<div class="explanation">{escape(t)}</div>' for t in texts)
    traces=render_trace_html(report.events)
    iframe='<iframe title="処理済みのイベントを再生する操作画面" sandbox="allow-scripts" srcdoc="{}"></iframe>'.format(html.escape(traces, quote=True))
    code=''.join('<details><summary>{} <span>実測 {:.3f}ms・SHA照合済</span></summary><pre><code>{}</code></pre></details>'.format(
        escape(event.function_id),event.elapsed_ms,escape(inspect_trace(event) or 'ソース版の照合に失敗したため表示しません。'))
        for event in report.events)
    facts=''.join(f'<div class="fact"><code>{escape(key)}</code><p>{escape(fact["text"])}</p></div>' for key,fact in report.facts.items())
    bridge=report.sections["S06_REVENUE_BRIDGE"]
    total_ms=sum(event.elapsed_ms for event in report.events)
    page=r'''<!doctype html><html lang="ja"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>KPI Investigator · Offline Preview</title>
<style>
:root{--navy:#0b2532;--muted:#637986;--teal:#06866e;--border:#e0e9ed;--bg:#f5f8fb;--red:#c95253;--blue:#578ac6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--navy);font-family:Inter,"Noto Sans JP",system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:auto;padding:0 24px 90px}header{display:flex;justify-content:space-between;align-items:center;padding:23px 0;border-bottom:1px solid var(--border);gap:12px}
.logo{font-weight:900;font-size:17px;letter-spacing:-.03em}.logo b{color:var(--teal)}.pill{font-size:11px;letter-spacing:.09em;border:1px solid #a5d6cd;color:#077462;background:#e2f5ef;border-radius:99px;padding:7px 13px;font-weight:800}
.hero{padding:50px 0 35px;display:flex;justify-content:space-between;align-items:end;gap:28px}.eyebrow{font-size:12px;color:#147b68;letter-spacing:.14em;font-weight:800;margin:0 0 12px}
h1{font-size:clamp(32px,5vw,52px);letter-spacing:-.055em;line-height:1.17;margin:0 0 14px}h2{font-size:23px;letter-spacing:-.035em;margin:0 0 20px}p{line-height:1.75}.lead{color:var(--muted);margin:0;max-width:690px}
.hero-date{background:white;padding:16px 18px;border:1px solid var(--border);border-radius:13px;min-width:200px;color:var(--muted);font-size:12px;box-shadow:0 7px 25px #0b25320a}.hero-date strong{display:block;font-size:23px;color:var(--navy)}
.section{margin:28px 0 42px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:10px;flex-wrap:wrap}.section-kicker{font-size:12px;color:var(--teal);font-weight:850;letter-spacing:.1em}.sub{font-size:13px;color:var(--muted);margin-top:-12px;margin-bottom:19px}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.metric{background:#fff;border:1px solid var(--border);border-radius:14px;padding:21px 23px;box-shadow:0 5px 15px #213a4810}.metric-label{font-size:13px;color:#71838d;font-weight:700}.metric-value{font-size:30px;letter-spacing:-.04em;font-weight:850;margin:11px 0 7px;white-space:nowrap}.unit{font-size:13px;color:#5b6d76;margin-left:6px}.metric-foot{font-size:12px;color:var(--muted)}.negative{color:var(--red)!important}.positive{color:#0d9273!important}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}.panel{background:white;border:1px solid var(--border);border-radius:15px;padding:25px;min-width:0;box-shadow:0 5px 15px #213a4807}.panel h3{font-size:18px;letter-spacing:-.02em;margin:0 0 16px}.panel p{font-size:13px;color:#71838d}
.insight{display:flex;gap:16px;align-items:flex-start;border-bottom:1px solid var(--border);padding:15px 0;font-weight:700;font-size:15px}.insight:last-child{border:0}.insight-id{font-size:13px;color:var(--teal);font-weight:900}.insight small{display:block;color:var(--muted);font-size:12px;font-weight:400;margin-top:7px}
.bar-row{display:grid;grid-template-columns:70px 1fr 92px;align-items:center;gap:13px;margin:19px 0;font-size:13px}.bar-name{font-weight:700}.bar-track{height:15px;background:#edf3f5;border-radius:99px;overflow:hidden}.bar-fill{height:100%;border-radius:99px}.bar-fill.down{background:#d9807a}.bar-fill.up{background:#5cb4a4}.bar-delta{text-align:right;font-weight:800;font-size:12px}
.strip{background:#edf7f3;border:1px solid #b8dfd0;border-radius:13px;padding:20px;margin-top:15px}.strip strong{font-size:21px;display:block;margin-top:5px}.strip small{display:block;margin-top:8px;color:#507d73}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:#697e89;font-weight:650;background:#f6fafb}td,th{padding:13px 9px;border-bottom:1px solid var(--border)}td:not(:first-child){text-align:right}th:not(:first-child){text-align:right}
.explanation{background:#f7fbfa;border-left:3px solid #80c3b1;border-radius:6px;padding:15px 17px;margin:10px 0;line-height:1.7;font-size:14px}.note{font-size:12px;color:var(--muted);line-height:1.65}.source-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.source{padding:18px;border:1px solid var(--border);background:#fff;border-radius:12px}.source strong{font-size:13px}.source p{font-size:12px;color:var(--muted);margin:8px 0 0}
iframe{width:100%;height:277px;border:0;border-radius:12px;display:block}.code-panel{margin-top:19px}details{background:#fff;border:1px solid var(--border);border-radius:9px;margin:7px 0;overflow:hidden}summary{cursor:pointer;padding:14px 17px;font-weight:750;font-size:13px}summary span{float:right;font-weight:400;color:var(--muted)}pre{overflow:auto;background:#0b1521;color:#c5e4da;margin:0;padding:20px;white-space:pre;font-size:12px;line-height:1.6}code{font-family:ui-monospace,Consolas,monospace}.fact{border-bottom:1px solid var(--border);padding:8px}.fact p{margin:4px 0 11px;color:var(--navy)}.foot{margin-top:45px;border-top:1px solid var(--border);padding-top:25px;color:var(--muted);font-size:12px;line-height:1.8}
@media(max-width:850px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.grid-2{grid-template-columns:1fr}.source-grid{grid-template-columns:repeat(2,1fr)}.hero{display:block}.hero-date{margin-top:20px;display:inline-block}}
@media(max-width:520px){.cards{grid-template-columns:1fr}.source-grid{grid-template-columns:1fr}.wrap{padding:0 15px 40px}.metric-value{font-size:26px}.panel{padding:17px}summary span{float:none;display:block;margin-top:5px}}
</style></head><body><div class="wrap">
<header><div class="logo">KPI <b>INVESTIGATOR</b> <span style="font-weight:400;color:#95a5ae;margin-left:10px">/ Python Analytics</span></div><div class="pill">OFFLINE · SYNTHETIC DATA</div></header>
<section class="hero"><div><p class="eyebrow">EXPLAINABLE DATA ANALYTICS · v0.7</p><h1>数字の変化を、<br>根拠までたどる。</h1><p class="lead">Pythonの7つの分析を実行して得た合成ECレポート。数字、推測、未確認事項、そして実際に実行したコードを分けて表示します。</p></div><div class="hero-date">対象月<strong>2026 / 09</strong>前月：2026 / 08</div></section>
<section class="section"><div class="section-head"><div><span class="section-kicker">01 / OVERVIEW</span><h2>KPIサマリー</h2></div><div class="note">前月比は2つの連続した完全暦月から算出</div></div><div class="cards">__CARDS__</div></section>
<div class="grid-2"><section class="panel"><span class="section-kicker">02 / IMPORTANT OBSERVATIONS</span><h3>注目すべき変化</h3>__INSIGHTS__<p>金額や注文数など異なる単位の数値を無理に順位づけず、独立した原因とは表現しません。</p></section>
<section class="panel"><span class="section-kicker">03 / REVENUE MOVEMENT</span><h3>チャネル別 売上差額</h3>__CHANNELS__<div class="strip">全体売上の前月差<strong>−130,000 円</strong><small>検索 −150,000円 + 広告 +20,000円 = −130,000円。商品別の差額とは合算しません。</small></div></section></div>
<section class="section grid-2"><article class="panel"><span class="section-kicker">04 / PRODUCT BREAKDOWN</span><h3>商品別分析</h3><table><thead><tr><th>商品</th><th>売上</th><th>注文数※</th><th>差額</th></tr></thead><tbody>__PRODUCTS__</tbody></table><p>※商品を含むユニーク注文の数。合計は全注文数と一致しない場合があります。</p></article><article class="panel"><span class="section-kicker">05 / DAILY CHANGE</span><h3>日次売上の変化（上位8件）</h3><table><thead><tr><th>日付</th><th>売上</th><th>前日差</th></tr></thead><tbody>__DAYS__</tbody></table><p>観測された連続日の差です。統計的異常や因果を示すものではありません。</p></article></section>
<section class="section"><div class="section-head"><div><span class="section-kicker">06 / EVIDENCE</span><h2>根拠と解釈の線引き</h2></div></div>
<div class="source-grid"><div class="source"><strong>確認できた事実</strong><p>Pythonの計算・日付の充足検査・事実IDを根拠に提示</p></div><div class="source"><strong>解釈・仮説</strong><p>定義済みコードと事実IDの一致を検査した固定説明</p></div><div class="source"><strong>外部調査</strong><p>未実施。検索結果や広告運用状況の裏取りはしていません</p></div><div class="source"><strong>現状では不明</strong><p>原因、CSVに書かれなかったチャネル、実データの完全性</p></div></div>
<div class="panel" style="margin-top:14px"><h3>事前生成済みの説明（ライブAIではありません）</h3>__EXPLANATIONS__<p>合成データのSHA-256・分析ロジック版・テンプレート版・許可済み事実IDを確認した結果だけを表示しています。実AI生成とは主張しません。</p></div></section>
<section class="section"><span class="section-kicker">07 / CODE TRACE</span><h2>実処理を、そのまま見せる。</h2><p class="sub">Pythonで実行した8関数のイベントを実測。下は処理済みの演出再生です。表示速度と実行時間は別です。</p>__TRACE__<div class="panel code-panel"><h3>実行したPython関数のソースコード</h3><p>ファイルをユーザーが任意指定できない許可リスト方式。実際の関数とSHA-256が一致したコードだけを展開できます。</p>__CODE__</div></section>
<section class="section"><details><summary>確認済みの数値根拠（fact_id）を開く</summary><div class="panel">__FACTS__</div></details></section>
<div class="foot">このHTMLはPythonの同じ分析エンジンから生成したローカル確認用プレビューです。Streamlit本体の実行、スマートフォン端末、公開URL、AI/Redis実接続を検証したものではありません。<br>合成デモに利用料金はかからず、ファイルを開いても外部APIへ接続しません。実コードは同梱ZIPの <code>kpi_investigator/</code> に含まれています。<br>観測したPython関数の実測合計：__TIMING__ ms。</div>
</div></body></html>'''
    for name,value in (("CARDS",''.join(cards)),("INSIGHTS",insights),("CHANNELS",channels),
                       ("PRODUCTS",prodrows),("DAYS",dayrows),("EXPLANATIONS",explanations),
                       ("TRACE",iframe),("CODE",code),("FACTS",facts),("TIMING",f'{total_ms:.3f}')):
        page=page.replace('__'+name+'__',value)
    path = ROOT / "preview.html"
    path.write_text(page,encoding="utf-8")
    return path


if __name__ == "__main__":
    result=build_preview()
    print(f'Created {result} ({result.stat().st_size} bytes)')
