"""Optional offline browser check. Requires local Playwright + Chromium.

This opens only a string from preview.html via set_content (no server, no API).
It does NOT test the Streamlit app, Windows or deployed hosting.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
p=Path(__file__).resolve().parents[1]
with sync_playwright() as play:
 b=play.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
 events=[]
 desktop=b.new_context(viewport={'width':1440,'height':900},device_scale_factor=1)
 page=desktop.new_page()
 page.on('request',lambda req:events.append(req.url))
 page.set_content((p/'preview.html').read_text('utf8'),wait_until='domcontentloaded')
 assert page.title()=='KPI Investigator · Offline Preview'
 assert page.locator('.metric').count()==6
 assert page.get_by_text('−130,000').count() >= 1
 assert page.locator('.explanation').count()==2
 assert page.locator('details').count()>=8
 first=page.locator('details').first
 first.click()
 assert first.get_attribute('open') is not None
 iframe=page.frame_locator('iframe[title="処理済みのイベントを再生する操作画面"]')
 assert iframe.locator('#normal').count()==1
 assert iframe.locator('#fast').count()==1
 assert iframe.locator('#skip').count()==1
 iframe.locator('#skip').click()
 assert '全件表示' in iframe.locator('#summary').inner_text()
 assert len([x for x in iframe.locator('#terminal').inner_text().splitlines() if 'S0' in x or 'load_dataset' in x])==8
 page.screenshot(path=str(p/'evidence'/'preview-desktop.png'),full_page=True)
 print('DESKTOP PASS: metrics=6, explanations=2, trace=8, source-click PASS, replay Skip PASS')
 m=b.new_context(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
 mobile=m.new_page()
 mobile.set_content((p/'preview.html').read_text('utf8'),wait_until='domcontentloaded')
 mobile.frame_locator('iframe').locator('#skip').click()
 assert mobile.locator('.metric').count()==6
 mobile.screenshot(path=str(p/'evidence'/'preview-mobile.png'),full_page=True)
 # Long code is scrollable within its own panel; the outer viewport shouldn't overflow.
 sizes=mobile.evaluate('({viewport: document.documentElement.clientWidth, doc: document.documentElement.scrollWidth})')
 print('MOBILE:',sizes,'same six KPIs / replay controls PASS')
 assert sizes['doc']<=sizes['viewport']+1,sizes
 print('NETWORK:',events)
 assert not events,events
 b.close()
