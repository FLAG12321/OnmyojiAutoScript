# This Python file uses the following encoding: utf-8
"""依赖声明门禁。

锁住一条关键约束：requirements 不得声明 onnxruntime 的任何发行版。

原因：onnxruntime 与 onnxruntime-directml 是两个发行版名但装同一个模块。
只要 requirements 里出现其中任意一个固定版本，每次启动执行
`pip install -r requirements.txt` 就会把另一个覆盖掉——GPU 加速会在
用户毫无察觉的情况下被降级成 CPU，或者 v6 模型因 ORT 版本过低直接加载失败。
onnxruntime 的发行版与版本统一由 deploy/ocr_deps.py 在运行期决定。
"""
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
REQUIREMENTS = ['requirements-in.txt', 'requirements.txt']
# 禁止被固定版本的 onnxruntime 发行版
ORT_DISTS = ('onnxruntime', 'onnxruntime-directml', 'onnxruntime-gpu',
             'onnxruntime-openvino')
# 已清退的 v5 运行时包
V5_DISTS = ('ppocr-onnx', 'onnxocr', 'ppocronnx')


def pinned_packages(rel_path: str) -> dict:
    """解析 requirements 中真实声明的包（跳过注释与 via 说明）。"""
    text = (ROOT / rel_path).read_text(encoding='utf-8')
    pinned = {}
    for line in text.splitlines():
        line = line.split('#')[0].strip()
        if not line or line.startswith('-'):
            continue
        match = re.match(r'^([A-Za-z0-9._-]+)\s*([=<>!~]=?.*)?$', line)
        if match:
            name = match.group(1).lower().replace('_', '-')
            pinned[name] = (match.group(2) or '').strip()
    return pinned


@pytest.mark.parametrize('rel_path', REQUIREMENTS)
@pytest.mark.parametrize('dist', ORT_DISTS)
def test_requirements_do_not_pin_onnxruntime(rel_path, dist):
    """requirements 不得声明任何 onnxruntime 发行版。"""
    pinned = pinned_packages(rel_path)
    assert dist not in pinned, (
        f'{rel_path} 声明了 {dist}，会在每次 pip install 时覆盖 '
        f'deploy/ocr_deps.py 选定的发行版'
    )


@pytest.mark.parametrize('rel_path', REQUIREMENTS)
@pytest.mark.parametrize('dist', V5_DISTS)
def test_requirements_do_not_pin_v5_packages(rel_path, dist):
    """v5 运行时包必须已从 requirements 移除。

    它们会通过传递依赖把 onnxruntime 拉回 1.16.3，而 1.16.3 无法加载
    PP-OCRv6 的 ONNX IR v10 模型。
    """
    pinned = pinned_packages(rel_path)
    assert dist not in pinned, f'{rel_path} 仍声明 v5 包 {dist}'


@pytest.mark.parametrize('rel_path', REQUIREMENTS)
def test_requirements_pin_rapidocr(rel_path):
    """rapidocr 必须固定版本：模型格式与调用契约随版本变化。"""
    from deploy.ocr_deps import RAPIDOCR_VERSION

    pinned = pinned_packages(rel_path)
    assert 'rapidocr' in pinned, f'{rel_path} 未声明 rapidocr'
    assert pinned['rapidocr'] == f'=={RAPIDOCR_VERSION}', \
        f'{rel_path} 的 rapidocr 版本与 ocr_deps.RAPIDOCR_VERSION 不一致'


def test_rapidocr_runtime_deps_are_pinned():
    """rapidocr 新引入的依赖必须锁版本，避免不同机器解析出不同版本。"""
    pinned = pinned_packages('requirements.txt')
    for dist in ('omegaconf', 'colorlog', 'antlr4-python3-runtime'):
        assert dist in pinned, f'requirements.txt 缺少 rapidocr 依赖 {dist}'
        assert pinned[dist].startswith('=='), f'{dist} 未固定版本'


def test_ort_version_floor_is_documented():
    """ORT 版本必须 >= 1.18：低于此版本无法加载 IR v10 模型。"""
    from deploy.ocr_deps import ORT_VERSION

    major, minor = (int(x) for x in ORT_VERSION.split('.')[:2])
    assert (major, minor) >= (1, 18), \
        f'ORT_VERSION={ORT_VERSION} 无法加载 PP-OCRv6 的 ONNX IR v10 模型'
