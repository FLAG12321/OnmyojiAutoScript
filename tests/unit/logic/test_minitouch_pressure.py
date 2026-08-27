# -*- coding: utf-8 -*-
"""minitouch 压力随机化（humanized 路径）单元测试。

背景：原实现 down/move 恒发 pressure=100，超过 MuMu 内置 minitouch 量程 50 被
钳到 1.0 —— MotionEvent 压力恒为 1.0 是框架层可读的注入指纹。改动后 humanized
路径按 [0.5, 0.95]×max_pressure 低频波动取值；off 档与 legacy 路径保持恒定。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from module.device.method.minitouch import Minitouch


class _Host:
    """最小宿主：只带 humanizer 与 max_pressure。"""

    def __init__(self, rng, max_pressure=50):
        self.humanizer = SimpleNamespace(rng=rng)
        self.max_pressure = max_pressure

    _humanized_pressure_seq = Minitouch._humanized_pressure_seq


@pytest.mark.unit
def test_pressure_seq_within_bounds():
    """压力值全部落在 [0.5, 0.95]×max 内且为整数。"""
    host = _Host(np.random.default_rng(42), max_pressure=50)
    seq = host._humanized_pressure_seq(200)
    assert seq is not None and len(seq) == 200
    assert all(isinstance(v, int) for v in seq)
    assert all(25 <= v <= 47 for v in seq), f'越界: min={min(seq)}, max={max(seq)}'


@pytest.mark.unit
def test_pressure_seq_varies():
    """序列有波动（非恒定），且相邻点变化幅度小（低频特征）。"""
    host = _Host(np.random.default_rng(7), max_pressure=50)
    seq = host._humanized_pressure_seq(100)
    assert len(set(seq)) > 5, '压力序列不应恒定'
    diffs = [abs(seq[i + 1] - seq[i]) for i in range(len(seq) - 1)]
    assert max(diffs) <= 5, f'相邻点跳变过大: {max(diffs)}'


@pytest.mark.unit
def test_pressure_seq_none_without_rng():
    """无 rng（测试桩/无 persona 上下文）返回 None，调用方保持恒定压力。"""
    host = _Host(None, max_pressure=50)
    assert host._humanized_pressure_seq(3) is None


@pytest.mark.unit
def test_pressure_seq_none_with_invalid_max():
    """量程非法（0/负）返回 None。"""
    host = _Host(np.random.default_rng(1), max_pressure=0)
    assert host._humanized_pressure_seq(3) is None


@pytest.mark.unit
def test_legacy_paths_keep_constant_pressure():
    """源码契约：legacy 路径不传 pressure 参数（恒为默认 100），off 档行为不变。"""
    src = open('module/device/method/minitouch.py', encoding='utf-8').read()
    # 三个 legacy impl 的方法体在 'pressure' 随机化之前不出现 pressure= 传参
    import re
    for name in ['_click_minitouch_legacy_impl', '_swipe_minitouch_legacy_impl',
                 '_long_click_minitouch_legacy_impl']:
        m = re.search(rf'def {name}\(.*?\n(?=    def )', src, re.S)
        assert m is not None, f'未找到 {name}'
        assert 'pressure=' not in m.group(0), f'{name} 不应传 pressure（legacy 恒定）'
