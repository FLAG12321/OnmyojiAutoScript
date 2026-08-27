"""人格生成、严格校验与持久化测试（Plan Task 3、4）。

人格是"这台机器的这个实例像哪个人"的持久化载体。它的两条硬要求：
1. 同 seed 完全可复现（否则黄金基线和降点重试都不可信）；
2. 损坏的 JSON 必须在 from_dict 就被拒绝，绝不带着非法人格进策略层
   ——非法权重要到 numpy 采样时才炸，那时已经在发手势的路上。
"""
import copy
import json
import math

import numpy as np
import pytest

from module.device.humanize.persona import (
    AIM_BIAS_RANGE,
    DEFAULT_WEIGHTS,
    PERSONA_VERSION,
    SCALAR_RANGES,
    Persona,
    PersonaInvalid,
)

pytestmark = pytest.mark.unit

SEED = 20260825


class TestGenerate:
    def test_same_seed_reproducible_excluding_created(self):
        """created 是持久化字段，不参与逻辑，必须从复现断言中排除（Spec §6.2）。"""
        a = Persona.generate(SEED).to_dict()
        b = Persona.generate(SEED).to_dict()
        a.pop('created')
        b.pop('created')
        assert a == b

    def test_created_is_frozen_at_generation(self):
        """to_dict() 不得动态取当前时间——否则同 seed 跨秒生成会得到不同 dict。"""
        p = Persona.generate(SEED, created='2026-08-25T10:00:00+08:00')
        assert p.to_dict()['created'] == '2026-08-25T10:00:00+08:00'
        assert p.to_dict()['created'] == p.to_dict()['created']

    def test_all_scalars_in_declared_range(self):
        for seed in range(30):
            p = Persona.generate(seed)
            for name, (lo, hi) in SCALAR_RANGES.items():
                assert lo <= getattr(p, name) <= hi, f'{name} 越界 (seed={seed})'
            for v in p.aim_bias:
                assert AIM_BIAS_RANGE[0] <= v <= AIM_BIAS_RANGE[1]
            assert p.arc_side in (-1, 1)

    def test_weight_dims_and_keys_match_defaults(self):
        p = Persona.generate(SEED)
        assert set(p.weights) == set(DEFAULT_WEIGHTS)
        for dim, opts in DEFAULT_WEIGHTS.items():
            assert set(p.weights[dim]) == set(opts), f'{dim} 方案 key 不一致'

    def test_weights_normalized_and_non_degenerate(self):
        """alpha 用 25 而非 8：浓度太低会采出"永远只选一个方案"的退化人格。"""
        for seed in range(40):
            p = Persona.generate(seed)
            for dim, opts in p.weights.items():
                assert sum(opts.values()) == pytest.approx(1.0)
                assert max(opts.values()) < 0.97, f'{dim} 退化 (seed={seed})'

    def test_does_not_touch_global_rng(self):
        """人格 RNG 必须独立；污染全局序列会破坏既有代码的随机行为（零回归的一部分）。"""
        np.random.seed(12345)
        before = np.random.random()
        np.random.seed(12345)
        Persona.generate(SEED)
        after = np.random.random()
        assert before == after

    def test_touch_liftoff_none_keeps_weight(self):
        """touch_liftoff.none 是"今天方案不进权重"的唯一显式例外（约 20% 不漂移）。"""
        assert 'none' in DEFAULT_WEIGHTS['touch_liftoff']
        assert DEFAULT_WEIGHTS['touch_liftoff']['none'] == pytest.approx(0.2)
        assert 'none' in Persona.generate(SEED).weights['touch_liftoff']

    def test_deprecated_today_options_absent(self):
        """其余"今天"方案不得进入权重表：uniform / same_point / fixed3 / fixed / none。"""
        assert 'uniform' not in DEFAULT_WEIGHTS['point']
        assert 'uniform' not in DEFAULT_WEIGHTS['press']
        assert 'same_point' not in DEFAULT_WEIGHTS['pointer_tail']
        assert 'fixed3' not in DEFAULT_WEIGHTS['swipe_tail']
        assert 'none' not in DEFAULT_WEIGHTS['dwell']
        assert 'none' not in DEFAULT_WEIGHTS['idle']


class TestRoundTrip:
    def test_generate_output_passes_own_validation(self):
        """契约 13：generate 的采样区间与 from_dict 的校验区间必须是同一张表。"""
        for seed in range(20):
            p = Persona.generate(seed)
            assert Persona.from_dict(p.to_dict()) == p


def _valid_raw() -> dict:
    return Persona.generate(SEED).to_dict()


class TestFromDictRejects:
    def test_version_mismatch(self):
        raw = _valid_raw()
        raw['version'] = PERSONA_VERSION + 1
        with pytest.raises(PersonaInvalid, match='version'):
            Persona.from_dict(raw)

    def test_missing_weight_dim(self):
        raw = _valid_raw()
        del raw['weights']['shape']
        with pytest.raises(PersonaInvalid, match='weights 维度'):
            Persona.from_dict(raw)

    def test_extra_weight_dim(self):
        raw = _valid_raw()
        raw['weights']['bogus'] = {'x': 1.0}
        with pytest.raises(PersonaInvalid, match='weights 维度'):
            Persona.from_dict(raw)

    def test_option_key_drift(self):
        """旧名 tail / touch_tail 已废弃；方案 key 与当前策略表不一致即视为损坏。"""
        raw = _valid_raw()
        raw['weights']['shape']['legacy_cbezier'] = 0.1
        with pytest.raises(PersonaInvalid, match='方案 key'):
            Persona.from_dict(raw)

    @pytest.mark.parametrize('bad', [-0.1, float('nan'), math.inf])
    def test_illegal_weight_value(self, bad):
        raw = _valid_raw()
        raw['weights']['press']['lognormal'] = bad
        with pytest.raises(PersonaInvalid, match='权重'):
            Persona.from_dict(raw)

    def test_all_zero_weight_dim(self):
        raw = _valid_raw()
        for k in raw['weights']['idle']:
            raw['weights']['idle'][k] = 0.0
        with pytest.raises(PersonaInvalid, match='权重和'):
            Persona.from_dict(raw)

    @pytest.mark.parametrize('bad', [0, 2, -2, 1.0])
    def test_illegal_arc_side(self, bad):
        raw = _valid_raw()
        raw['arc_side'] = bad
        with pytest.raises(PersonaInvalid, match='arc_side'):
            Persona.from_dict(raw)

    @pytest.mark.parametrize('name', list(SCALAR_RANGES))
    def test_scalar_zero_rejected(self, name):
        raw = _valid_raw()
        raw[name] = 0
        with pytest.raises(PersonaInvalid, match=name):
            Persona.from_dict(raw)

    @pytest.mark.parametrize('name', list(SCALAR_RANGES))
    def test_scalar_out_of_range_rejected(self, name):
        raw = _valid_raw()
        raw[name] = SCALAR_RANGES[name][1] * 10
        with pytest.raises(PersonaInvalid, match=name):
            Persona.from_dict(raw)

    def test_aim_bias_wrong_length(self):
        raw = _valid_raw()
        raw['aim_bias'] = [0.1]
        with pytest.raises(PersonaInvalid, match='aim_bias'):
            Persona.from_dict(raw)

    def test_aim_bias_out_of_range(self):
        raw = _valid_raw()
        raw['aim_bias'] = [0.9, 0.0]
        with pytest.raises(PersonaInvalid, match='aim_bias'):
            Persona.from_dict(raw)

    def test_missing_required_field(self):
        raw = _valid_raw()
        del raw['press_median']
        with pytest.raises(PersonaInvalid, match='press_median'):
            Persona.from_dict(raw)

    def test_weights_not_a_mapping(self):
        raw = _valid_raw()
        raw['weights'] = []
        with pytest.raises(PersonaInvalid, match='weights'):
            Persona.from_dict(raw)


class TestPersonaStore:
    def test_creates_file_when_absent(self, tmp_path):
        from module.device.humanize.persona import PersonaStore
        store = PersonaStore('oas', base_dir=str(tmp_path))
        p = store.load_or_create()
        assert store.path.exists()
        assert json.loads(store.path.read_text(encoding='utf-8'))['seed'] == p.seed

    def test_second_load_returns_same_persona(self, tmp_path):
        """人格必须跨重启稳定——每次启动换一个人等于没有人格。"""
        from module.device.humanize.persona import PersonaStore
        store = PersonaStore('oas', base_dir=str(tmp_path))
        first = store.load_or_create()
        second = PersonaStore('oas', base_dir=str(tmp_path)).load_or_create()
        assert second == first

    def test_config_names_are_isolated(self, tmp_path):
        from module.device.humanize.persona import PersonaStore
        a = PersonaStore('oas', base_dir=str(tmp_path)).load_or_create()
        b = PersonaStore('oas1', base_dir=str(tmp_path)).load_or_create()
        assert a.seed != b.seed or a.created != b.created
        assert (tmp_path / 'humanize_persona_oas.json').exists()
        assert (tmp_path / 'humanize_persona_oas1.json').exists()

    def test_filename_convention(self, tmp_path):
        from module.device.humanize.persona import PersonaStore
        store = PersonaStore('oas1', base_dir=str(tmp_path))
        assert store.path.name == 'humanize_persona_oas1.json'

    @pytest.mark.parametrize('content', [
        '{"version": 1, "trunc',           # 截断 JSON
        '{}',                              # 空对象
        '[]',                              # 类型错误
        '{"version": 0, "seed": 1}',       # 旧 version
    ])
    def test_rebuilds_on_corrupt_file(self, tmp_path, content):
        from module.device.humanize.persona import PersonaStore
        store = PersonaStore('oas', base_dir=str(tmp_path))
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(content, encoding='utf-8')
        p = store.load_or_create()
        assert p.version == PERSONA_VERSION
        # 重建结果必须已落盘，且能被再次读取
        assert Persona.from_dict(json.loads(store.path.read_text(encoding='utf-8'))) == p

    def test_rebuilds_on_illegal_weights(self, tmp_path):
        from module.device.humanize.persona import PersonaStore
        store = PersonaStore('oas', base_dir=str(tmp_path))
        store.path.parent.mkdir(parents=True, exist_ok=True)
        raw = Persona.generate(SEED).to_dict()
        raw['weights']['press']['lognormal'] = -1.0
        store.path.write_text(json.dumps(raw), encoding='utf-8')
        p = store.load_or_create()
        assert all(v >= 0 for v in p.weights['press'].values())

    def test_write_failure_does_not_block(self, tmp_path, monkeypatch):
        """写盘失败只记 warning，用内存人格继续——拟人化不值得炸掉任务流程。"""
        from module.device.humanize import persona as persona_mod
        store = persona_mod.PersonaStore('oas', base_dir=str(tmp_path))

        def boom(path, data):
            raise OSError('disk full')

        monkeypatch.setattr(persona_mod, '_write_json_atomic', boom)
        p = store.load_or_create()
        assert p.version == PERSONA_VERSION
        assert not store.path.exists()

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        from module.device.humanize.persona import PersonaStore
        store = PersonaStore('oas', base_dir=str(tmp_path))
        store.load_or_create()
        assert list(tmp_path.glob('*.tmp')) == []
