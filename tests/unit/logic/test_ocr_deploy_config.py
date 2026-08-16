# This Python file uses the following encoding: utf-8
"""PP-OCRv6 部署配置项单元测试。

覆盖 deploy/config.py 的 OCR 段新增默认值、deploy/template 的键位同步，
以及 OcrModelDir 必须解析到项目目录内（不允许把模型下到用户级缓存）。
全部只读临时 deploy.yaml，不触碰真实 config/deploy.yaml。
"""
import os

import pytest

from deploy.config import ConfigModel, DeployConfig
from deploy.utils import DEPLOY_TEMPLATE, poor_yaml_read

pytestmark = pytest.mark.unit

# OCR 段应有的默认值：键 -> 默认值
OCR_DEFAULTS = {
    'UseOcrServer': True,
    'StartOcrServer': True,
    'OcrServerPort': 22268,
    'OcrClientAddress': '127.0.0.1:22268',
    'OcrDevice': 'auto',
    'OcrModelType': 'small',
    'OcrModelDir': './toolkit/ocr_models',
    'OcrCpuThreads': 4,
    'OcrAutoAlignDeps': True,
}


@pytest.fixture
def deploy_config(tmp_path):
    """用临时 deploy.yaml 构造 DeployConfig，隔离真实部署配置。"""
    deploy_file = tmp_path / 'deploy.yaml'
    deploy_file.write_text('Deploy:\n  Git:\n    Branch: master\n', encoding='utf-8')
    return DeployConfig(file=str(deploy_file))


@pytest.mark.parametrize('key,expected', sorted(OCR_DEFAULTS.items()))
def test_config_model_ocr_defaults(key, expected):
    """ConfigModel 必须声明 OCR 段全部键，且默认值与设计一致。"""
    assert hasattr(ConfigModel, key), f'ConfigModel 缺少 OCR 配置项 {key}'
    assert getattr(ConfigModel, key) == expected


@pytest.mark.parametrize('key,expected', sorted(OCR_DEFAULTS.items()))
def test_template_declares_ocr_keys(key, expected):
    """deploy/template 必须同步声明 OCR 键。

    poor_yaml_write 是按键名正则替换写回的，模板缺键会导致用户配置无法落盘；
    show_config 还会用 config_template[k] 取默认值，缺键直接 KeyError。
    """
    template = poor_yaml_read(DEPLOY_TEMPLATE)
    assert key in template, f'deploy/template 缺少 OCR 配置项 {key}'
    assert template[key] == expected


def test_ocr_device_and_model_type_are_valid_choices():
    """设备与档位默认值必须落在受支持的枚举内。"""
    assert ConfigModel.OcrDevice in ('auto', 'dml', 'cpu')
    assert ConfigModel.OcrModelType in ('small', 'medium')


def test_ocr_model_dir_resolves_inside_project(deploy_config):
    """OcrModelDir 必须能被 filepath() 解析，且落在项目根目录内。"""
    resolved = deploy_config.filepath('OcrModelDir')
    root = deploy_config.root_filepath
    assert os.path.isabs(resolved)
    assert resolved.startswith(root), f'{resolved} 不在项目目录 {root} 内'
    assert resolved.rstrip('/').endswith('toolkit/ocr_models')


def test_user_deploy_yaml_can_override_ocr_keys(tmp_path):
    """用户在 deploy.yaml 覆写 OCR 键时必须生效并回写。"""
    deploy_file = tmp_path / 'deploy.yaml'
    deploy_file.write_text(
        'Deploy:\n'
        '  Ocr:\n'
        '    OcrDevice: cpu\n'
        '    OcrModelType: medium\n'
        '    OcrCpuThreads: 8\n',
        encoding='utf-8',
    )
    config = DeployConfig(file=str(deploy_file))
    assert config.OcrDevice == 'cpu'
    assert config.OcrModelType == 'medium'
    assert config.OcrCpuThreads == 8

    # write() 已在 __init__ 中执行过，重读应保持覆写值
    reread = poor_yaml_read(str(deploy_file))
    assert reread['OcrDevice'] == 'cpu'
    assert reread['OcrModelType'] == 'medium'
    assert reread['OcrCpuThreads'] == 8
