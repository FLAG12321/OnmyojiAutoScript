# This Python file uses the following encoding: utf-8
"""PP-OCRv6 真实模型集成测试。

需要已执行过 `./toolkit/python.exe -m deploy.ocr_deps`（依赖与模型就位）。
未就位时整体跳过，不让缺依赖的机器把回归跑成红灯。

这里验证的是单元测试用 fake engine 无法覆盖的部分：
真实模型的返回契约、状态泄漏、GPU/CPU 解析、以及真实截图上的识别结果。
"""
import os
import pathlib
import subprocess
import sys

import numpy as np
import pytest

pytestmark = [pytest.mark.integration]

ROOT = pathlib.Path(__file__).resolve().parents[1].parent
MODEL_DIR = ROOT / 'toolkit' / 'ocr_models'


def _deps_ready() -> bool:
    """依赖与模型是否就位。

    不按 dist 名查 onnxruntime：装的可能是 onnxruntime 也可能是
    onnxruntime-directml（两个发行版名，同一个模块），直接查模块更可靠。
    """
    try:
        import importlib.metadata as md
        import importlib.util
        md.version('rapidocr')
        if importlib.util.find_spec('onnxruntime') is None:
            return False
    except Exception:
        return False
    if not MODEL_DIR.is_dir():
        return False
    files = [f.lower() for f in os.listdir(MODEL_DIR) if f.endswith('.onnx')]
    return any('det' in f for f in files) and any('rec' in f for f in files)


pytestmark.append(
    pytest.mark.skipif(not _deps_ready(),
                       reason='PP-OCRv6 依赖或模型未就位，先运行 python -m deploy.ocr_deps')
)


@pytest.fixture(scope='module')
def model():
    """真实 v6 模型，模块级复用（加载昂贵）。"""
    from module.ocr.rapid_ocr import RapidOcrModel

    return RapidOcrModel(model_dir=str(MODEL_DIR), device='auto',
                         model_type='small', cpu_threads=4)


def text_image(text='54/40', width=420, height=110):
    """动态生成测试图，不依赖仓库里的图片资产。"""
    import cv2

    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.putText(image, text, (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 2,
                (0, 0, 0), 4, cv2.LINE_AA)
    return image


# ---------------- 设备解析 ----------------

def test_device_resolves_to_supported_value(model):
    """auto 必须解析到 dml 或 cpu，两者都算通过（取决于机器有无 DX12 GPU）。"""
    assert model.resolved_device in ('dml', 'cpu')


def test_engine_builds_and_reports_device(model, capsys):
    """引擎能真的建起来（会触发 provider 校验与预热推理）。"""
    assert model.engine is not None
    # 预热成功后设备不再变化
    assert model.resolved_device in ('dml', 'cpu')


# ---------------- 返回契约 ----------------

def test_single_line_reads_generated_text(model):
    text, score = model.ocr_single_line(text_image('54/40'))
    assert text == '54/40', f'单行识别得到 {text!r}'
    assert score > 0.8


def test_full_mode_reads_generated_text(model):
    results = model.detect_and_ocr(text_image('OCR TEST'))
    assert results, '全图模式未检出任何文本'
    joined = ''.join(r.ocr_text for r in results).replace(' ', '')
    assert 'OCR' in joined.upper()


def test_full_mode_box_is_indexable(model):
    """RuleList 依赖 box[0][0] / box[0][1]。"""
    box = model.detect_and_ocr(text_image('12345'))[0].box
    assert isinstance(box[0][0], (int, float))
    assert isinstance(box[0][1], (int, float))


def test_empty_screen_returns_empty_results(model):
    blank = np.full((200, 400, 3), 255, dtype=np.uint8)
    assert model.detect_and_ocr(blank) == []
    text, score = model.ocr_single_line(blank)
    assert text == ''
    assert score == pytest.approx(0.0, abs=1e-6)


def test_zero_size_image_does_not_crash(model):
    empty = np.zeros((0, 100, 3), dtype=np.uint8)
    assert model.detect_and_ocr(empty) == []
    assert model.ocr_single_line(empty) == ('', 0.0)


# ---------------- 状态泄漏（真实引擎） ----------------

def test_alternating_calls_keep_full_mode_working(model):
    """全图 -> 单行 -> 全图 -> 单行 -> 全图，三次全图结果必须一致。

    这是 RapidOCR update_params 状态泄漏的回归门禁：如果适配器漏传
    use_det，第二次全图会退化成单行模式，静默返回单条无框结果。
    """
    image = text_image('12345')
    first = [r.ocr_text for r in model.detect_and_ocr(image)]
    assert first, '首次全图未检出文本'

    model.ocr_single_line(text_image('54/40'))
    second = [r.ocr_text for r in model.detect_and_ocr(image)]

    model.ocr_single_line(text_image('54/40'))
    third = [r.ocr_text for r in model.detect_and_ocr(image)]

    assert first == second == third, \
        f'全图结果发生漂移: {first} / {second} / {third}'


def test_single_line_still_works_after_full(model):
    model.detect_and_ocr(text_image('OCR TEST'))
    text, _ = model.ocr_single_line(text_image('54/40'))
    assert text == '54/40'


def test_tuning_params_do_not_persist(model):
    """一次调低阈值后，下一次默认调用必须回到默认阈值。"""
    image = text_image('12345')
    baseline = len(model.detect_and_ocr(image))
    model.detect_and_ocr(image, drop_score=0.01, box_thresh=0.1)
    assert len(model.detect_and_ocr(image)) == baseline


# ---------------- 隔离进程资源 ----------------

@pytest.mark.slow
def test_isolated_ocr_process_memory():
    """单独进程里跑一次 OCR，常驻内存必须在可接受范围内。

    多开时每个实例可能各自持有一份模型，因此这个上限直接决定可开实例数。
    """
    script = (
        'import numpy as np, os, psutil, sys;'
        'sys.path.insert(0, r"%s");'
        'from module.ocr.rapid_ocr import RapidOcrModel;'
        'm = RapidOcrModel(model_dir=r"%s", device="auto", model_type="small", cpu_threads=4);'
        'img = np.full((110, 420, 3), 255, dtype=np.uint8);'
        'm.detect_and_ocr(img);'
        'm.ocr_single_line(img);'
        'print("RSS_MB=%%.1f" %% (psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024))'
    ) % (str(ROOT), str(MODEL_DIR))

    out = subprocess.run([sys.executable, '-c', script], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=300)
    line = next((l for l in out.stdout.splitlines() if l.startswith('RSS_MB=')), None)
    assert line, f'未取到 RSS。stdout={out.stdout[-500:]} stderr={out.stderr[-500:]}'
    rss = float(line.split('=')[1])
    assert rss < 900, f'隔离 OCR 进程常驻内存 {rss}MB 过高'
