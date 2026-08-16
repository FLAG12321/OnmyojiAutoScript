# This Python file uses the following encoding: utf-8
"""项目自有 OCR 结果类型单元测试。

要求业务层不再依赖第三方（ppocronnx / onnxocr）的结果类，
且 base_ocr / atom.list / Exploration 三处消费点都指向项目内类型。
"""
import ast
import pathlib

import numpy as np
import pytest

from module.ocr.result import BoxedResult

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
# 业务层消费 BoxedResult 的文件，必须从 module.ocr.result 导入
CONSUMERS = [
    'module/ocr/base_ocr.py',
    'module/ocr/rpc.py',
    'module/atom/list.py',
    'tasks/Exploration/base.py',
]
# 禁止在运行时代码中出现的第三方 OCR 包
FORBIDDEN_MODULES = ('ppocronnx', 'onnxocr')


def test_boxed_result_holds_four_fields():
    """构造顺序与旧第三方类保持一致：box, text_img, ocr_text, score。"""
    box = np.array([[1, 2], [3, 4], [5, 6], [7, 8]])
    result = BoxedResult(box, None, 'abc', 0.87)
    assert result.box is box
    assert result.text_img is None
    assert result.ocr_text == 'abc'
    assert result.score == pytest.approx(0.87)


def test_boxed_result_score_is_float():
    """score 传入 numpy 标量或字符串时统一转成 float，便于与阈值比较。"""
    result = BoxedResult([[0, 0]], None, 'x', np.float32(0.5))
    assert isinstance(result.score, float)
    assert result.score == pytest.approx(0.5)


def test_boxed_result_ocr_text_is_mutable():
    """BaseCor.detect_and_ocr 会就地改写 ocr_text，必须允许赋值。"""
    result = BoxedResult([[0, 0]], None, 'raw', 0.9)
    result.ocr_text = 'processed'
    assert result.ocr_text == 'processed'


def test_boxed_result_repr_contains_text_and_score():
    """便于日志排查：repr 里能看到文本与分数。"""
    text = repr(BoxedResult([[0, 0]], None, '灵气', 0.66))
    assert '灵气' in text
    assert '0.66' in text


def _imported_modules(path: pathlib.Path) -> set[str]:
    """用 AST 提取真实 import 的顶级模块名，避免注释/字符串误命中。"""
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


@pytest.mark.parametrize('rel_path', CONSUMERS)
def test_consumers_import_project_boxed_result(rel_path):
    """消费点必须从项目内 module.ocr.result 取 BoxedResult。"""
    source = (ROOT / rel_path).read_text(encoding='utf-8')
    assert 'BoxedResult' in source, f'{rel_path} 未使用 BoxedResult'
    assert 'from module.ocr.result import BoxedResult' in source, \
        f'{rel_path} 未从 module.ocr.result 导入 BoxedResult'


@pytest.mark.parametrize('rel_path', CONSUMERS)
def test_consumers_do_not_import_third_party_ocr(rel_path):
    """消费点不得再 import 第三方 OCR 包。"""
    modules = _imported_modules(ROOT / rel_path)
    for module in modules:
        top = module.split('.')[0]
        assert top not in FORBIDDEN_MODULES, \
            f'{rel_path} 仍在 import 第三方 OCR 包 {module}'
