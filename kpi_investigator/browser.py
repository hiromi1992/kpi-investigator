"""Browser-local anonymous ID for a future approved quota-enabled deployment."""

from pathlib import Path


def get_anonymous_id() -> str | None:
    import streamlit.components.v1 as components
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "anonymous_id"
    component = components.declare_component("kpi_browser_id", path=str(frontend))
    value = component(key="kpi_browser_id", default=None)
    return value if isinstance(value, str) and len(value) == 32 else None
