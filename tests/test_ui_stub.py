"""Executes Streamlit app code via a lightweight stand-in, NOT a real browser test."""

import runpy
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Context:
    def __enter__(self):return self
    def __exit__(self, *_):return False
    def __getattr__(self, name):return lambda *args,**kwargs:None


class FakeStreamlit(types.ModuleType):
    def __init__(self, *, click=True, initial_source="合成デモ（推奨)"):
        super().__init__("streamlit")
        self.session_state = {}
        self.click=click
        self.source=initial_source
        self.messages=[]
        self.replays=[]
    def __getattr__(self, method):
        if method in {"spinner","container"}: return lambda *a,**k:Context()
        if method=="expander":return lambda *a,**k:(self.messages.append((method,a,k)) or Context())
        if method=="columns":return lambda n,**k:[Context() for _ in range(n)]
        if method=="tabs":return lambda opts,**k:[Context() for _ in opts]
        if method=="radio":return lambda *a,**k:self.source
        if method=="selectbox":return lambda title,opts,**kw:opts[kw.get("index",0)] if opts and kw.get("index") is not None else None
        if method=="button":return lambda *a,**k:self.click
        if method=="checkbox":return lambda *a,**k:False
        if method=="file_uploader":return lambda *a,**k:None
        if method in {"set_page_config","markdown","caption","metric","success","info","warning","error","code","dataframe","write","divider"}:
            def record(*args,**kwargs):
                if method!="set_page_config":self.messages.append((method,args,kwargs))
            return record
        raise AttributeError(method)


def test_app_renders_offline_demo_by_one_click_without_streamlit_dependency(monkeypatch):
    st=FakeStreamlit()
    comp=types.ModuleType("streamlit.components")
    v1=types.ModuleType("streamlit.components.v1")
    v1.html=lambda html,**kw:st.replays.append(html)
    comp.v1=v1
    st.components=comp
    monkeypatch.setitem(sys.modules,"streamlit",st)
    monkeypatch.setitem(sys.modules,"streamlit.components",comp)
    monkeypatch.setitem(sys.modules,"streamlit.components.v1",v1)
    runpy.run_path(str(ROOT/"app.py"),run_name="__main__")
    assert "last_report" in st.session_state
    r=st.session_state["last_report"]
    assert r.context.comparable and len(r.events)==8
    assert len(st.replays)==1 and "Skip／全件表示" in st.replays[0]
    messages="\n".join(str(args) for _,args,__ in st.messages)
    assert "670,000" in messages
    assert "800,000" not in messages or "2026-08" in messages
    assert "実行した関数のソースコード" in messages
    assert "事前生成済みの説明（ライブAIではありません）" in messages
    assert "チャネル間に差があります" not in messages
    assert any(m=="code" and "s01_kpi_summary" in str(args) for m,args,_ in st.messages)
    assert not any(m=="error" for m,_,__ in st.messages)


def test_app_clears_stale_state_on_upload_switch_without_recalculation(monkeypatch):
    st=FakeStreamlit()
    comp=types.ModuleType("streamlit.components")
    v1=types.ModuleType("streamlit.components.v1")
    v1.html=lambda html,**kw:st.replays.append(html)
    comp.v1=v1
    st.components=comp
    for key,val in (("streamlit",st),("streamlit.components",comp),("streamlit.components.v1",v1)):
        monkeypatch.setitem(sys.modules,key,val)
    runpy.run_path(str(ROOT/"app.py"),run_name="__main__")
    assert st.session_state.get("last_report") is not None
    st.source="自分のCSV 2ファイル";st.click=False
    runpy.run_path(str(ROOT/"app.py"),run_name="__main__")
    assert "last_report" not in st.session_state
