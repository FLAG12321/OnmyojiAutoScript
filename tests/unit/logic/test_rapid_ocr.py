# This Python file uses the following encoding: utf-8
"""PP-OCRv6 适配器单元测试（RapidOcrModel）。

全程用 fake engine，不加载真实模型、不联网、不依赖显卡，
因此可以在任意机器上作为门禁运行。真实模型验证见 tests/integration/test_ocr_v6.py。

重点覆盖三件事：
1. 返回契约：单行返回 (text, score)，全图返回 list[BoxedResult]，空结果归一化。
2. 状态复位：RapidOCR 的 update_params() 会把 use_det 永久写回实例，
   单行调用后再全图必须仍然走检测，否则全图 OCR 会静默失效。
3. 设备分流：auto 探测失败降级 CPU，dml 强制 GPU，cpu 强制 CPU，
   medium 档位在 CPU 下自动降级为 small。
"""
import numpy as np
import pytest

from module.ocr.rapid_ocr import RapidOcrModel
from module.ocr.result import BoxedResult

pytestmark = pytest.mark.unit


class FakeFullOutput:
    """模拟 RapidOCR 全图模式返回的 RapidOCROutput。"""

    def __init__(self, boxes, txts, scores):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


class FakeRecOutput:
    """模拟 RapidOCR 单行模式返回的 TextRecOutput（没有 boxes 属性）。"""

    def __init__(self, txts, scores):
        self.txts = txts
        self.scores = scores


class FakeEngine:
    """记录每次调用参数的假引擎，用来断言参数被显式传满。"""

    def __init__(self, full_boxes=1):
        self.calls = []
        self.full_boxes = full_boxes
        # 模拟真实 RapidOCR 的状态泄漏：把调用参数写回实例
        self.state = {}

    def __call__(self, image, **kwargs):
        self.state.update(kwargs)
        self.calls.append(dict(kwargs))
        use_det = self.state.get('use_det', True)
        if not use_det:
            return FakeRecOutput(('单行文本',), (0.95,))
        boxes = np.array([[[0, 0], [10, 0], [10, 8], [0, 8]]] * self.full_boxes)
        txts = tuple(f'第{i}行' for i in range(self.full_boxes))
        scores = tuple([0.9] * self.full_boxes)
        return FakeFullOutput(boxes, txts, scores)


def make_model(engine=None, device='cpu', model_type='small', **kwargs):
    """构造被测适配器，默认注入 fake engine 避免加载真实模型。"""
    return RapidOcrModel(
        model_dir='./toolkit/ocr_models',
        device=device,
        model_type=model_type,
        engine=engine if engine is not None else FakeEngine(),
        **kwargs,
    )


def image(h=40, w=200):
    return np.zeros((h, w, 3), dtype=np.uint8)


# ---------------- 返回契约 ----------------

def test_ocr_single_line_returns_text_and_float_score():
    model = make_model()
    text, score = model.ocr_single_line(image())
    assert text == '单行文本'
    assert isinstance(score, float)
    assert score == pytest.approx(0.95)


def test_detect_and_ocr_returns_boxed_results():
    model = make_model(FakeEngine(full_boxes=3))
    results = model.detect_and_ocr(image())
    assert len(results) == 3
    assert all(isinstance(r, BoxedResult) for r in results)
    assert results[0].ocr_text == '第0行'
    assert results[0].score == pytest.approx(0.9)


def test_detect_and_ocr_box_supports_two_level_index():
    """RuleList 会取 box[0][0] / box[0][1]，box 必须支持二级下标。"""
    model = make_model()
    box = model.detect_and_ocr(image())[0].box
    assert box[0][0] == 0
    assert box[0][1] == 0


def test_empty_screen_full_mode_returns_empty_list():
    """空画面时 RapidOCR 返回 boxes/txts/scores 全 None，必须归一化为 []。"""

    class EmptyEngine(FakeEngine):
        def __call__(self, image, **kwargs):
            self.calls.append(dict(kwargs))
            return FakeFullOutput(None, None, None)

    model = make_model(EmptyEngine())
    assert model.detect_and_ocr(image()) == []


def test_empty_screen_single_line_returns_empty_text():
    """空画面单行返回 ('',) / (0.0,)，归一化为 ('', 0.0)。"""

    class EmptyEngine(FakeEngine):
        def __call__(self, image, **kwargs):
            self.calls.append(dict(kwargs))
            return FakeRecOutput(('',), (0.0,))

    model = make_model(EmptyEngine())
    assert model.ocr_single_line(image()) == ('', 0.0)


def test_none_result_is_normalized():
    """引擎返回 None 时不得抛异常，单行给 ('', 0.0)，全图给 []。"""

    class NoneEngine(FakeEngine):
        def __call__(self, image, **kwargs):
            self.calls.append(dict(kwargs))
            return None

    model = make_model(NoneEngine())
    assert model.ocr_single_line(image()) == ('', 0.0)
    assert model.detect_and_ocr(image()) == []


@pytest.mark.parametrize('shape', [(0, 100, 3), (100, 0, 3), (0, 0, 3)])
def test_zero_size_image_skips_engine(shape):
    """零尺寸图直接返回空结果，不调用引擎（RapidOCR 会在内部除零）。"""
    engine = FakeEngine()
    model = make_model(engine)
    empty = np.zeros(shape, dtype=np.uint8)
    assert model.ocr_single_line(empty) == ('', 0.0)
    assert model.detect_and_ocr(empty) == []
    assert engine.calls == []


def test_none_image_returns_empty():
    """截图失败传入 None 时返回空结果，不抛异常。"""
    engine = FakeEngine()
    model = make_model(engine)
    assert model.ocr_single_line(None) == ('', 0.0)
    assert model.detect_and_ocr(None) == []
    assert engine.calls == []


# ---------------- 状态复位 ----------------

MUTABLE_KEYS = (
    'use_det', 'use_cls', 'use_rec',
    'return_word_box', 'return_single_char_box',
    'text_score', 'box_thresh', 'unclip_ratio',
)


def test_every_call_passes_all_mutable_params():
    """每次调用必须显式传满全部可变参数，否则会继承上次调用的状态。"""
    engine = FakeEngine()
    model = make_model(engine)
    model.ocr_single_line(image())
    model.detect_and_ocr(image())
    assert len(engine.calls) == 2
    for call in engine.calls:
        for key in MUTABLE_KEYS:
            assert key in call, f'调用缺少必须显式复位的参数 {key}'


def test_use_det_flag_matches_mode():
    engine = FakeEngine()
    model = make_model(engine)
    model.ocr_single_line(image())
    assert engine.calls[-1]['use_det'] is False
    model.detect_and_ocr(image())
    assert engine.calls[-1]['use_det'] is True


def test_alternating_calls_do_not_leak_state():
    """全图 -> 单行 -> 全图 -> 单行 -> 全图，三次全图结果必须完全一致。"""
    engine = FakeEngine(full_boxes=4)
    model = make_model(engine)
    first = [r.ocr_text for r in model.detect_and_ocr(image())]
    model.ocr_single_line(image())
    second = [r.ocr_text for r in model.detect_and_ocr(image())]
    model.ocr_single_line(image())
    third = [r.ocr_text for r in model.detect_and_ocr(image())]
    assert first == second == third
    assert len(first) == 4


def test_use_cls_always_disabled():
    """OAS 截图恒正向，方向分类只会增加开销和误判。"""
    engine = FakeEngine()
    model = make_model(engine)
    model.detect_and_ocr(image())
    model.ocr_single_line(image())
    assert all(call['use_cls'] is False for call in engine.calls)


# ---------------- 参数转发与过滤 ----------------

def test_detect_and_ocr_forwards_tuning_params():
    """SixRealms 需要下调阈值，参数必须透传到引擎。"""
    engine = FakeEngine()
    model = make_model(engine)
    model.detect_and_ocr(image(), drop_score=0.1, box_thresh=0.2, unclip_ratio=2.0)
    call = engine.calls[-1]
    assert call['text_score'] == pytest.approx(0.1)
    assert call['box_thresh'] == pytest.approx(0.2)
    assert call['unclip_ratio'] == pytest.approx(2.0)


def test_detect_and_ocr_uses_defaults_when_params_omitted():
    """不传调优参数时使用适配器默认值，而不是 None（None 会被引擎当成缺省状态）。"""
    engine = FakeEngine()
    model = make_model(engine)
    model.detect_and_ocr(image())
    call = engine.calls[-1]
    assert call['text_score'] is not None
    assert call['box_thresh'] is not None
    assert call['unclip_ratio'] is not None


def test_detect_and_ocr_drops_low_score_results():
    """低于 drop_score 的结果必须被过滤，避免上层拿到垃圾文本。"""

    class MixedEngine(FakeEngine):
        def __call__(self, image, **kwargs):
            self.calls.append(dict(kwargs))
            boxes = np.array([[[0, 0], [1, 0], [1, 1], [0, 1]]] * 2)
            return FakeFullOutput(boxes, ('高分', '低分'), (0.9, 0.05))

    model = make_model(MixedEngine())
    results = model.detect_and_ocr(image(), drop_score=0.5)
    assert [r.ocr_text for r in results] == ['高分']


def test_vertical_mode_rotates_crops_before_recognition():
    """竖排模式必须旋转裁剪图后再识别，否则竖排文字识别率极低。"""
    engine = FakeEngine()
    model = make_model(engine)
    tall = np.zeros((60, 30, 3), dtype=np.uint8)
    rotated = model.rotate_vertical(tall)
    assert rotated.shape[0] == 30 and rotated.shape[1] == 60
    wide = np.zeros((30, 60, 3), dtype=np.uint8)
    assert model.rotate_vertical(wide).shape == wide.shape


# ---------------- 设备分流 ----------------

def test_cpu_device_disables_gpu_providers():
    model = make_model(device='cpu')
    params = model.build_params()
    assert params['EngineConfig.onnxruntime.use_dml'] is False
    assert params['EngineConfig.onnxruntime.use_cuda'] is False
    assert params['EngineConfig.onnxruntime.intra_op_num_threads'] == 4


def test_dml_device_enables_directml():
    model = make_model(device='dml')
    params = model.build_params()
    assert params['EngineConfig.onnxruntime.use_dml'] is True


def test_auto_device_falls_back_to_cpu_when_dml_unavailable(monkeypatch):
    """无独显/驱动不支持的机器上 auto 必须降级 CPU，不能启动失败。"""
    monkeypatch.setattr('module.ocr.rapid_ocr.probe_directml', lambda: False)
    model = make_model(device='auto')
    assert model.resolved_device == 'cpu'
    assert model.build_params()['EngineConfig.onnxruntime.use_dml'] is False


def test_auto_device_uses_dml_when_available(monkeypatch):
    monkeypatch.setattr('module.ocr.rapid_ocr.probe_directml', lambda: True)
    model = make_model(device='auto')
    assert model.resolved_device == 'dml'
    assert model.build_params()['EngineConfig.onnxruntime.use_dml'] is True


def test_medium_downgrades_to_small_on_cpu():
    """medium 仅在 GPU 下启用；CPU 上自动降级为 small 而不是拒绝启动。"""
    model = make_model(device='cpu', model_type='medium')
    assert model.resolved_model_type == 'small'


def test_medium_kept_on_gpu(monkeypatch):
    monkeypatch.setattr('module.ocr.rapid_ocr.probe_directml', lambda: True)
    model = make_model(device='auto', model_type='medium')
    assert model.resolved_device == 'dml'
    assert model.resolved_model_type == 'medium'


def test_invalid_device_raises():
    with pytest.raises(ValueError):
        make_model(device='cuda')


def test_invalid_model_type_raises():
    with pytest.raises(ValueError):
        make_model(model_type='tiny')


def test_params_pin_v6_and_model_dir():
    """必须锁定 PP-OCRv6 与项目内模型目录，避免下载到用户级缓存。

    build_params 返回的是纯字符串字典（便于在未安装 rapidocr 的机器上测试），
    枚举转换由 _to_engine_params 在构造引擎前完成。
    """
    model = make_model()
    params = model.build_params()
    assert params['Global.model_root_dir'] == './toolkit/ocr_models'
    assert params['Global.use_cls'] is False
    assert params['Det.ocr_version'] == 'PP-OCRv6'
    assert params['Rec.ocr_version'] == 'PP-OCRv6'
    assert params['Det.model_type'] == 'small'
    assert params['Rec.model_type'] == 'small'
    assert params['Det.engine_type'] == 'onnxruntime'
    assert params['Rec.rec_batch_num'] == 2


def test_medium_params_use_medium_model_type(monkeypatch):
    """GPU 下 medium 档位必须真的写进 det/rec 参数。"""
    monkeypatch.setattr('module.ocr.rapid_ocr.probe_directml', lambda: True)
    params = make_model(device='auto', model_type='medium').build_params()
    assert params['Det.model_type'] == 'medium'
    assert params['Rec.model_type'] == 'medium'
