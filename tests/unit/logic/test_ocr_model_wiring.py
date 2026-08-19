# This Python file uses the following encoding: utf-8
"""OCR 模型来源接线单元测试。

覆盖 module/ocr/models.py 的单引擎工厂、本地/RPC 分流、
BaseCor 的参数转发接口，以及 SixRealms 竖排改用稳定参数接口。
全程注入假引擎与假配置，不加载真实模型、不起 RPC 服务。
"""
import ast
import pickle
import pathlib
import sys
import time
import types

import numpy as np
import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def clean_cache():
    """每个用例前后清空模型缓存，避免用例间互相污染。"""
    from module.ocr import models
    models.clear_ocr_model_cache()
    yield
    models.clear_ocr_model_cache()


def fake_deploy_config(tmp_path, **overrides):
    """构造最小可用的假 deploy 配置对象。"""
    values = {
        'UseOcrServer': False,
        'OcrClientAddress': '127.0.0.1:22268',
        'OcrDevice': 'cpu',
        'OcrModelType': 'small',
        'OcrCpuThreads': 4,
    }
    values.update(overrides)
    config = types.SimpleNamespace(**values)
    # filepath 在真实 DeployConfig 里把相对路径解析成项目内绝对路径
    config.filepath = lambda key: str(tmp_path)
    return config


@pytest.fixture
def patch_state(monkeypatch, tmp_path):
    """把 models.py 读取的 State.deploy_config 换成假配置。"""

    def apply(**overrides):
        from module.server.setting import State
        monkeypatch.setattr(State, 'deploy_config',
                            fake_deploy_config(tmp_path, **overrides))
        return tmp_path

    return apply


# ---------------- 本地模型工厂 ----------------

def test_get_local_ocr_model_returns_rapid_ocr_model(patch_state):
    from module.ocr.models import get_local_ocr_model
    from module.ocr.rapid_ocr import RapidOcrModel

    patch_state()
    model = get_local_ocr_model('ch')
    assert isinstance(model, RapidOcrModel)


def test_get_local_ocr_model_uses_deploy_config(patch_state):
    """设备/档位/线程数/模型目录必须来自 deploy 配置。"""
    from module.ocr.models import get_local_ocr_model

    model_dir = patch_state(OcrDevice='cpu', OcrModelType='small', OcrCpuThreads=6)
    model = get_local_ocr_model('ch')
    assert model.model_dir == str(model_dir)
    assert model.device == 'cpu'
    assert model.model_type == 'small'
    assert model.cpu_threads == 6


def test_get_local_ocr_model_is_cached(patch_state):
    """同一语言必须复用实例，否则每个 Rule 都会加载一份模型。"""
    from module.ocr.models import get_local_ocr_model

    patch_state()
    assert get_local_ocr_model('ch') is get_local_ocr_model('ch')


def test_get_local_ocr_model_rejects_unsupported_lang(patch_state):
    from module.ocr.models import get_local_ocr_model

    patch_state()
    with pytest.raises(ValueError):
        get_local_ocr_model('en')


def test_local_model_is_not_created_eagerly(patch_state):
    """工厂只应在被调用时构造模型，import models 不得触发加载。"""
    from module.ocr import models

    patch_state()
    assert models._LOCAL_MODEL_CACHE == {}


# ---------------- 本地 / RPC 分流 ----------------

def test_get_ocr_model_uses_local_when_server_disabled(patch_state):
    from module.ocr.models import get_local_ocr_model, get_ocr_model

    patch_state(UseOcrServer=False)
    assert get_ocr_model('ch') is get_local_ocr_model('ch')


def test_get_ocr_model_uses_proxy_when_server_enabled(patch_state, monkeypatch):
    from module.ocr import models

    patch_state(UseOcrServer=True, OcrClientAddress='127.0.0.1:22999')
    created = []

    def fake_proxy(address):
        created.append(address)
        return types.SimpleNamespace(address=address, is_proxy=True)

    monkeypatch.setattr(models, '_create_model_proxy', fake_proxy)
    model = models.get_ocr_model('ch')
    assert model.is_proxy is True
    assert created == ['127.0.0.1:22999']


def test_proxy_is_cached_per_address(patch_state, monkeypatch):
    from module.ocr import models

    patch_state(UseOcrServer=True)
    calls = []
    monkeypatch.setattr(models, '_create_model_proxy',
                        lambda address: calls.append(address) or types.SimpleNamespace(address=address))
    models.get_ocr_model('ch')
    models.get_ocr_model('ch')
    assert len(calls) == 1


def test_proxy_failure_falls_back_to_local(patch_state, monkeypatch):
    """RPC 连不上时必须降级本地模型，不能让整个任务崩掉。"""
    from module.ocr import models
    from module.ocr.rapid_ocr import RapidOcrModel

    patch_state(UseOcrServer=True)

    def broken_proxy(address):
        raise RuntimeError('connection refused')

    monkeypatch.setattr(models, '_create_model_proxy', broken_proxy)
    model = models.get_ocr_model('ch')
    assert isinstance(model, RapidOcrModel)


def test_clear_cache_drops_both_caches(patch_state, monkeypatch):
    from module.ocr import models

    patch_state(UseOcrServer=True)
    monkeypatch.setattr(models, '_create_model_proxy',
                        lambda address: types.SimpleNamespace(address=address))
    models.get_ocr_model('ch')
    assert models._OCR_PROXY_CACHE
    models.clear_ocr_model_cache()
    assert models._OCR_PROXY_CACHE == {}
    assert models._LOCAL_MODEL_CACHE == {}


# ---------------- BaseCor 参数转发 ----------------

class RecordingModel:
    """记录 detect_and_ocr 收到的参数。"""

    def __init__(self):
        self.kwargs = None

    def ocr_single_line(self, image):
        return '文本', 0.99

    def detect_and_ocr(self, image, **kwargs):
        self.kwargs = kwargs
        from module.ocr.result import BoxedResult
        return [BoxedResult([[1, 2], [3, 2], [3, 6], [1, 6]], None, '结果', 0.9)]


def make_cor(cls=None):
    from module.ocr.base_ocr import BaseCor

    cls = cls or BaseCor
    cor = cls(name='ocr_test', mode='Full', method='Default',
              roi=(0, 0, 100, 40), area=(0, 0, 100, 40), keyword='')
    model = RecordingModel()
    # model 是 cached_property，直接写实例字典即可注入
    cor.__dict__['model'] = model
    return cor, model


def test_base_cor_detect_and_ocr_forwards_kwargs():
    cor, model = make_cor()
    cor.detect_and_ocr(np.zeros((40, 100, 3), dtype=np.uint8),
                       drop_score=0.1, box_thresh=0.2, vertical=True)
    assert model.kwargs == {'drop_score': 0.1, 'box_thresh': 0.2, 'vertical': True}


def test_base_cor_detect_and_ocr_without_kwargs():
    """不传参时不得凭空注入参数，保持原有默认行为。"""
    cor, model = make_cor()
    cor.detect_and_ocr(np.zeros((40, 100, 3), dtype=np.uint8))
    assert model.kwargs == {}


def test_base_cor_detect_text_still_works():
    cor, model = make_cor()
    assert cor.detect_text(np.zeros((40, 100, 3), dtype=np.uint8)) == '结果'


# ---------------- SixRealms 竖排 ----------------

def test_vertical_text_uses_stable_params():
    """VerticalText 必须通过参数接口下调阈值，不再改引擎内部对象。"""
    from tasks.SixRealms.oas_ocr import VerticalText

    cor, model = make_cor(VerticalText)
    cor.detect_and_ocr(np.zeros((40, 100, 3), dtype=np.uint8))
    assert model.kwargs == {'drop_score': 0.1, 'box_thresh': 0.2, 'vertical': True}


# ---------------- Single 竖排 fallback ----------------

class _VerticalFallbackModel:
    """竖排文本：ocr_single_line(use_det=False) 识别为空，detect_and_ocr 兜底识别。"""

    def __init__(self):
        self.detect_called = False

    def ocr_single_line(self, image):
        return '', 0.0

    def detect_and_ocr(self, image, **kwargs):
        from module.ocr.result import BoxedResult
        self.detect_called = True
        return [BoxedResult([[0, 0], [1, 0], [1, 1], [0, 1]], None, '幽火姥姥', 0.9)]


def _make_single_cor(model):
    from module.ocr.sub_ocr import Single

    cor = Single(name='ocr_vertical', mode='Single', method='Default',
                 roi=(0, 0, 46, 175), area=(0, 0, 46, 175), keyword='')
    cor.__dict__['model'] = model
    return cor


def test_ocr_single_vertical_fallback():
    """竖排文字必须走 detect 兜底：ocr_single_line 返回空时由 detect_and_ocr 识别。"""
    model = _VerticalFallbackModel()
    cor = _make_single_cor(model)
    # 46x175 的竖排裁剪图
    assert cor.ocr_single(np.zeros((175, 46, 3), dtype=np.uint8)) == '幽火姥姥'
    assert model.detect_called


def test_ocr_single_horizontal_no_fallback():
    """横排文字直接由 ocr_single_line 识别，不触发 detect 兜底。"""
    class _HorizontalModel:
        def ocr_single_line(self, image):
            return '横排文字', 0.99

        def detect_and_ocr(self, image, **kwargs):
            raise AssertionError('横排文字不应走 detect 兜底')

    cor = _make_single_cor(_HorizontalModel())
    assert cor.ocr_single(np.zeros((40, 100, 3), dtype=np.uint8)) == '横排文字'


def test_vertical_text_allows_caller_override():
    """调用方显式传参时应覆盖默认值，而不是重复传参报错。"""
    from tasks.SixRealms.oas_ocr import VerticalText

    cor, model = make_cor(VerticalText)
    cor.detect_and_ocr(np.zeros((40, 100, 3), dtype=np.uint8), drop_score=0.3)
    assert model.kwargs['drop_score'] == pytest.approx(0.3)
    assert model.kwargs['vertical'] is True


def test_vertical_text_no_longer_touches_engine_internals():
    """不得再访问旧引擎的内部属性。

    用 AST 扫描真实属性访问节点，注释与文档字符串里提到这些名字不算命中。
    """
    tree = ast.parse((ROOT / 'tasks/SixRealms/oas_ocr.py').read_text(encoding='utf-8'))
    accessed = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for forbidden in ('text_detector', 'text_recognizer', 'partial', 'is_proxy'):
        assert forbidden not in accessed, f'oas_ocr.py 仍在访问 {forbidden}'


# ---------------- RPC 服务端模型来源 ----------------

def test_rpc_server_uses_v6_factory(monkeypatch):
    """OcrServer 应延迟到首次 OCR 请求时从统一工厂取 v6 模型。"""
    from module.ocr import rpc

    sentinel = object()
    monkeypatch.setattr(rpc, '_get_server_model', lambda: sentinel)
    server = rpc.OcrServer()
    assert server.model is None
    assert server.ping() is True
    assert server._acquire_model() is sentinel
    server._release_request()


def test_rpc_server_loads_on_request_and_releases_when_idle(monkeypatch):
    """RPC 监听常驻，但 OCR 模型按请求加载并可在空闲后释放。"""
    from module.ocr import models, rpc

    class FakeModel:
        def ocr_single_line(self, image):
            return 'ok', 0.99

    model = FakeModel()
    monkeypatch.setattr(rpc, '_get_server_model', lambda: model)
    monkeypatch.setattr(models, 'clear_ocr_model_cache', lambda: None)

    server = rpc.OcrServer(idle_timeout=0.01, idle_check_interval=0.01)
    assert server.model is None
    assert server.ping() is True
    assert server.ocr_single_line(pickle.dumps(np.zeros((2, 2), dtype=np.uint8))) == ('ok', 0.99)
    assert server.model is model

    deadline = time.monotonic() + 1
    while server.model is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.model is None
    assert server.ping() is True


def test_rpc_server_releases_model_when_all_instances_become_idle(monkeypatch):
    """所有实例注销任务后应立即释放模型，不必等待十分钟兜底计时器。"""
    from module.ocr import models, rpc

    sentinel = object()
    monkeypatch.setattr(rpc, '_get_server_model', lambda: sentinel)
    monkeypatch.setattr(models, 'clear_ocr_model_cache', lambda: None)

    server = rpc.OcrServer(idle_timeout=60, idle_check_interval=60)
    assert server.set_instance_active('oas1', True) is True
    assert server._acquire_model() is sentinel
    server._release_request()
    assert server.model is sentinel

    assert server.set_instance_active('oas1', False) is True
    assert server.model is None
    assert server.ping() is True


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


@pytest.mark.parametrize('rel_path', [
    'module/ocr/models.py',
    'module/ocr/rpc.py',
    'module/ocr/base_ocr.py',
])
def test_ocr_wiring_does_not_import_v5(rel_path):
    """接线文件不得 import v5 引擎，否则 import 链会提前加载旧后端。"""
    modules = _imported_modules(ROOT / rel_path)
    forbidden = {'onnxocr', 'ppocronnx',
                 'module.ocr.ppocr', 'module.ocr.onnx_paddle_ocr'}
    assert not (modules & forbidden), f'{rel_path} 仍在 import v5: {modules & forbidden}'


def test_importing_ocr_chain_does_not_load_v5_backend():
    """导入完整 OCR 调用链后，v5 后端不得出现在 sys.modules。"""
    import module.ocr.base_ocr  # noqa: F401
    import module.ocr.models  # noqa: F401
    import module.ocr.rpc  # noqa: F401
    for name in list(sys.modules):
        top = name.split('.')[0]
        assert top not in ('onnxocr', 'ppocronnx'), f'v5 后端被提前加载: {name}'
