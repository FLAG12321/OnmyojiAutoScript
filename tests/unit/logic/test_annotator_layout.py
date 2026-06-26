from pathlib import Path


ANNOTATOR_ROOT = Path(__file__).resolve().parents[3] / "module" / "server" / "web" / "annotator"


def read_widget(name: str) -> str:
    # 读取标注工具拆分后的 HTML 片段，避免测试依赖完整页面挂载流程。
    return (ANNOTATOR_ROOT / "static" / "widget" / name).read_text(encoding="utf-8")


def read_css(name: str) -> str:
    # 读取页面样式文件，用静态契约覆盖布局类名和滚动策略。
    return (ANNOTATOR_ROOT / "static" / "css" / name).read_text(encoding="utf-8")


def read_js(name: str) -> str:
    # 读取前端脚本，覆盖关键函数声明，避免局部替换破坏原生 JS 结构。
    return (ANNOTATOR_ROOT / "static" / "js" / name).read_text(encoding="utf-8")


def test_roi_inputs_live_in_rule_detail_below_rule_fields():
    center_panel = read_widget("center-panel.html")
    right_window = read_widget("right-window.html")

    assert "roiFrontValue" not in center_panel
    assert "roiBackValue" not in center_panel
    assert "roiFields" in right_window
    assert right_window.index('id="ruleFields"') < right_window.index('id="roiFields"')


def test_center_output_is_split_between_log_and_preview_compare():
    center_panel = read_widget("center-panel.html")

    assert "output-preview-layout" in center_panel
    assert "outputLog" in center_panel
    assert "roiPreviewCurrent" in center_panel
    assert "roiPreviewSaved" in center_panel
    assert center_panel.index('id="outputLog"') < center_panel.index('id="roiPreviewCompare"')


def test_stage_wrap_hides_scaled_overflow():
    css = read_css("center-panel.css")

    stage_wrap_start = css.index(".stage-wrap")
    stage_wrap_end = css.index(".stage-canvas-holder", stage_wrap_start)
    stage_wrap_block = css[stage_wrap_start:stage_wrap_end]

    assert "overflow: hidden;" in stage_wrap_block
    assert "overflow: auto;" not in stage_wrap_block


def test_annotator_script_keeps_roi_update_functions_declared():
    js = read_js("app.js")

    assert "function refreshRoiLayoutFromRule()" in js
    assert "function updateRuleFromForm(changedField = \"\")" in js
    assert "function collectRulesPayload()" in js
    assert js.index("function refreshRoiLayoutFromRule()") < js.index("function setupRoiBox")
    assert js.index("function updateRuleFromForm(changedField = \"\")") < js.index("async function refreshRulesList")
    assert js.index("function collectRulesPayload()") < js.index("async function persistRules")


def test_output_and_preview_stay_side_by_side():
    css = read_css("center-panel.css")

    layout_start = css.index(".output-preview-layout")
    layout_end = css.index(".output-log-column", layout_start)
    layout_block = css[layout_start:layout_end]

    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);" in layout_block
    assert ".output-preview-layout {\n    grid-template-columns: 1fr;" not in css
    assert ".output-preview-layout {\n    grid-template-rows:" not in css


def test_annotator_script_refreshes_current_and_saved_roi_previews():
    js = read_js("app.js")

    assert "function refreshCurrentRoiPreview()" in js
    assert "context.drawImage(el.mainImage" in js
    assert "function refreshSavedRoiPreview()" in js
    assert "currentRulePreviewUrl(rule)" in js
    assert "function refreshRoiPreviewCompare()" in js
    assert "await refreshActiveRuleImageExists();\n    refreshRoiPreviewCompare();" in js


def test_annotator_static_assets_use_cache_busting_urls():
    index = (ANNOTATOR_ROOT / "index.html").read_text(encoding="utf-8")
    main_js = read_js("main.js")

    assert 'href="/tool/annotator/static/css/base.css?v=' in index
    assert 'src="/tool/annotator/static/js/main.js?v=' in index
    assert "?v=${STATIC_VERSION}" in main_js


def test_base_css_does_not_keep_legacy_canvas_or_output_rules():
    css = read_css("base.css")

    assert ".stage-wrap" not in css
    assert ".image-stage" not in css
    assert "#mainImage" not in css
    assert ".output-panel" not in css
    assert ".center-output-panel" not in css
