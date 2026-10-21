"""RED tests: blankdash copilot must render Plotly specs, not only html.

Same bug class as the chat visualizations fix (docs/testing/chat-visuals.tdd.md):
`VizResult.html` defaults to "", so a chart whose figure serializes to a `spec`
but has no legacy CDN-`<script>` html is currently dropped by the `v && v.html`
filters in the copilot stream, and neither the copilot preview nor the
"Add to canvas" widget knows how to plot a spec.

Contract tests follow the repo's source-grep convention (see
tests/test_chat_visuals.py).  Run: python -m pytest tests/test_blankdash_viz.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLANKDASH_JS = ROOT / "web" / "js" / "pages" / "blankdash.js"
N = 1400  # window size for a function-body slice


def _src() -> str:
    return BLANKDASH_JS.read_text(encoding="utf-8")


def _window(marker: str, size: int = N) -> str:
    src = _src()
    i = src.find(marker)
    assert i != -1, f"marker not found: {marker!r}"
    return src[i:i + size]


def test_stream_filters_accept_spec_only_viz():
    """Both the streaming and /ask fallback collectors must keep spec-only viz."""
    src = _src()
    assert src.count("v.html || v.spec") >= 2, (
        "the copilot stream still filters visualizations with `v && v.html` — "
        "charts that carry only a Plotly `spec` are silently dropped"
    )


def test_viz_lib_stores_spec_with_html():
    """_copilotVizBubbleHtml must store {html, spec} instead of html alone."""
    block = _window("_copilotVizBubbleHtml: function")
    assert "spec: v.spec" in block, (
        "_copilotVizLib must keep the Plotly spec alongside the legacy html"
    )


def test_spec_render_helper_exists():
    """A single spec-rendering helper mirrors chat.js `_renderVizSpecs`."""
    assert "_renderVizSpec: function" in _src(), (
        "blankdash needs a _renderVizSpec(host, spec) helper using Plotly.newPlot"
    )


def test_hydrate_renders_spec_before_html():
    """The copilot preview must plot specs, not only re-execute html scripts."""
    block = _window("_copilotHydrateVizs")
    assert "_renderVizSpec" in block, (
        "_copilotHydrateVizs must render stored specs via the Plotly helper"
    )


def test_canvas_path_carries_spec():
    """"Add to canvas" must pass the spec through to the widget config."""
    to_canvas = _window("_copilotVizToCanvas: function")
    assert "spec" in to_canvas, (
        "_copilotVizToCanvas must forward the stored spec to _addCopilotWidget"
    )
    widget = _window("_addCopilotWidget: function")
    assert "spec: spec" in widget or "spec: (spec" in widget, (
        "the custom widget config must persist `spec` so it can re-plot on load"
    )


def test_custom_widget_renderer_plots_spec():
    """The custom widget branch must Plotly.newPlot a persisted spec."""
    block = _window('if (w.type === "custom")')
    assert "_renderVizSpec" in block and "spec" in block, (
        "the custom widget renderer must plot config.spec before falling back "
        "to config.html"
    )
