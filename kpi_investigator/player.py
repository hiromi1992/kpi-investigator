"""Safe, self-contained replay of already measured events (never executes Python)."""

from __future__ import annotations

import json

from kpi_investigator.trace import SOURCE_ALLOWLIST, TraceEvent


HTML_PLAYER = r'''<!doctype html>
<html lang="ja"><head><meta charset="UTF-8"/>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;background:#0b1521;color:#d6f3ea;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;min-height:255px}
.shell{padding:20px 22px;border:1px solid #304b57;border-radius:12px;height:100%}
.top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.tag{color:#89edd2;font-size:11px;letter-spacing:.12em}.label{color:#acb9c7;font-size:12px}
.controls{display:flex;gap:6px;margin:12px 0}button{color:#ecfff8;background:#213240;border:1px solid #42606a;border-radius:6px;padding:7px 13px;cursor:pointer}
button:hover,button:focus{background:#326559;outline:2px solid #9fecde}
#terminal{height:120px;overflow:auto;white-space:pre-wrap;font-size:12px;line-height:1.8}
#bar{height:4px;border-radius:3px;background:#263740;overflow:hidden}#fill{height:100%;width:0;background:#74ddae;transition:width .15s}
</style></head><body><div class="shell">
<div class="top"><span class="tag">▸ PYTHON EXECUTION TRACE</span><span class="label">処理済みの記録を再生（計算は再実行しません）</span></div>
<div class="controls"><button id="normal">1x 再生</button><button id="fast">2x 再生</button><button id="skip">Skip／全件表示</button></div>
<div id="terminal" role="log" aria-live="polite"></div><div id="bar"><div id="fill"></div></div>
<div class="label" id="summary"></div></div>
<script>
const events=__TRACE_JSON__;
const terminal=document.querySelector('#terminal'), summary=document.querySelector('#summary'), fill=document.querySelector('#fill');
const total=events.reduce((n,e)=>n+e.elapsed_ms,0);let timer=null,shown=0;
function reset(){if(timer!==null)clearTimeout(timer);timer=null;shown=0;terminal.textContent='';fill.style.width='0%';}
function append(){const e=events[shown];if(!e)return;
  const line=String(shown+1).padStart(2,'0')+'  '+e.id+'  '+e.description+'  [実測 '+e.elapsed_ms.toFixed(3)+' ms]';
  terminal.textContent+=line+'\n';shown++;fill.style.width=(100*shown/Math.max(1,events.length))+'%';
  terminal.scrollTop=terminal.scrollHeight;}
function play(speed){reset();const step=()=>{if(shown>=events.length){summary.textContent='再生完了 · 実処理合計 '+total.toFixed(3)+' ms ／ 表示用再生速度 '+speed+'x';return;}
 append();timer=setTimeout(step, 950/speed);};step();summary.textContent='実処理合計 '+total.toFixed(3)+' ms ／ 再生速度 '+speed+'x（演出）';}
function skip(){reset();while(shown<events.length)append();summary.textContent='全件表示 · 実処理合計 '+total.toFixed(3)+' ms ／ 再生は省略しました';}
document.querySelector('#normal').addEventListener('click',()=>play(1));
document.querySelector('#fast').addEventListener('click',()=>play(2));
document.querySelector('#skip').addEventListener('click',skip);
if(window.matchMedia('(prefers-reduced-motion: reduce)').matches)skip();else play(1);
</script></body></html>'''


def render_trace_html(events: list[TraceEvent]) -> str:
    """Send strictly allowlisted IDs and measured numbers; never uploaded text."""
    items = []
    for event in events:
        if event.function_id not in SOURCE_ALLOWLIST:
            continue
        # Labels are internal hardcoded descriptions; exclude dynamic input values.
        items.append({"id": event.function_id, "description": {
            "load_dataset": "CSVを検証", "S01_KPI_SUMMARY": "主要指標を計算", "S02_PERIOD_COMPARISON": "月次比較を計算",
            "S03_CHANNEL": "チャネル集計", "S04_PRODUCT": "商品集計", "S05_GROWTH_DECLINE": "増減ランキング",
            "S06_REVENUE_BRIDGE": "売上差額を検算", "S07_DAILY_CHANGE": "日次変化を計算",
        }[event.function_id], "elapsed_ms": round(max(0.0, min(event.elapsed_ms, 1_000_000)), 3)})
    encoded = json.dumps(items, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    return HTML_PLAYER.replace("__TRACE_JSON__", encoded)
