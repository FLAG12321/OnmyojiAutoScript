# This Python file uses the following encoding: utf-8
# @brief    后端 i18n 缺失 key 自动补齐的纯逻辑单元测试
#           对应设计文档 docs/superpowers/specs/2026-07-29-i18n-sync-design.md（OASX 仓库）

import json
import pytest

from module.server.i18n import I18n


def fake_script_task(task_name):
    """模拟 ConfigModel.script_task 的返回形状；Broken 任务模拟 schema 异常"""
    if task_name == 'Broken':
        raise ValueError('bad schema')
    return {
        'scheduler': [
            {'name': 'enable', 'type': 'boolean'},
            {'name': 'success_interval',
             'description': 'success_interval_help', 'type': 'string'},
        ],
        'demo_config': [
            {'name': 'battle_mode', 'enumEnum': ['mode_a', 'mode_b'], 'type': 'enum'},
            {'name': 'empty_desc_field', 'description': '', 'type': 'string'},
        ],
    }


MENU = {'Daily': ['Demo', 'Broken']}


class TestCollectFrontendKeys:
    def test_collects_all_key_kinds(self):
        # 应收集：菜单分组名/任务名/args 分组名/字段 name/description/enum 值
        keys = I18n.collect_frontend_keys(MENU, fake_script_task)
        assert {'Daily', 'Demo', 'scheduler', 'demo_config',
                'enable', 'success_interval', 'success_interval_help',
                'battle_mode', 'mode_a', 'mode_b'} <= keys
        # 空 description 不产生空 key 垃圾条目
        assert '' not in keys
        assert 'empty_desc_field' in keys

    def test_broken_task_skipped_but_name_kept(self):
        # 单个任务 schema 抛异常：跳过其字段收集，但任务名本身仍收集，且不向外抛
        keys = I18n.collect_frontend_keys(MENU, fake_script_task)
        assert 'Broken' in keys

    def test_dynamic_enum_values_not_collected(self):
        # handle 与 leader_instance 都按运行环境动态枚举，候选值不应进入翻译表。
        def task_with_handle(task_name):
            return {
                'device': [
                    {'name': 'handle', 'description': 'handle_help', 'type': 'enum',
                     'enumEnum': ['', '27272 (0,0)']},
                    {'name': 'screenshot_method', 'type': 'enum',
                     'enumEnum': ['window_background']},
                ],
                'orochi_config': [
                    {'name': 'leader_instance', 'description': 'leader_instance_help',
                     'type': 'enum', 'enumEnum': ['', 'OAS1', 'OAS2']},
                ],
            }

        keys = I18n.collect_frontend_keys({'Daily': ['Demo']}, task_with_handle)
        # 字段名与 description 照常收集（它们是稳定 key）
        assert {'handle', 'handle_help'} <= keys
        assert {'leader_instance', 'leader_instance_help'} <= keys
        # 动态候选项一律不收
        assert not any('27272' in k for k in keys)
        assert 'OAS1' not in keys
        assert 'OAS2' not in keys
        # 其余字段的静态枚举值不受影响
        assert 'window_background' in keys


class TestLoadAdditionsFilter:
    def test_empty_values_filtered(self, tmp_path, monkeypatch):
        # 空值条目（历史占位/手工误删内容）不应下发给前端（.tr 回退显示 key 原文而非空白）
        (tmp_path / 'zh-CN.json').write_text(
            json.dumps({'a': '甲', 'b': ''}, ensure_ascii=False), encoding='utf-8')
        (tmp_path / 'en-US.json').write_text('{}', encoding='utf-8')
        monkeypatch.setattr(I18n, 'assets_i18n_dir', tmp_path)
        data = I18n.load_additions()
        assert data['zh-CN'] == {'a': '甲'}
        assert data['en-US'] == {}

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        # 文件不存在时返回空 dict，不抛异常（保持原行为）
        monkeypatch.setattr(I18n, 'assets_i18n_dir', tmp_path)
        data = I18n.load_additions()
        assert data == {'en-US': {}, 'zh-CN': {}}


class TestSyncMissingKeys:
    @pytest.fixture
    def i18n_files(self, tmp_path, monkeypatch):
        """搭建镜像文件与 assets 翻译文件的临时环境"""
        mirror = tmp_path / 'mirror' / 'zh-CN.json'
        mirror.parent.mkdir(parents=True)
        # 镜像（前端内置翻译）已覆盖 enable；Demo 同时存在于镜像与 assets（均命中分支）
        mirror.write_text(
            json.dumps({'enable': '启用', 'Demo': '演示任务'}, ensure_ascii=False),
            encoding='utf-8')
        assets_dir = tmp_path / 'assets'
        assets_dir.mkdir()
        # assets 已有 Demo 与 old_key
        (assets_dir / 'zh-CN.json').write_text(
            json.dumps({'Demo': '演示任务', 'old_key': '旧翻译'}, ensure_ascii=False),
            encoding='utf-8')
        monkeypatch.setattr(I18n, 'file_zh_cn', mirror)
        monkeypatch.setattr(I18n, 'assets_i18n_dir', assets_dir)
        return assets_dir / 'zh-CN.json'

    def test_appends_only_missing_keys(self, i18n_files):
        count = I18n.sync_missing_keys(MENU, fake_script_task)
        data = json.loads(i18n_files.read_text(encoding='utf-8'))
        assert count > 0
        # 缺失 key 以 key 原文为占位值追加（显示效果与未翻译一致，等待人工填中文）
        assert data['success_interval'] == 'success_interval'
        assert data['mode_a'] == 'mode_a'
        # assets 已有条目不动、顺序保留（原有两条仍在最前）
        # Demo 同时存在于 assets 与镜像（均命中），值保持 assets 原文
        assert data['Demo'] == '演示任务'
        assert list(data)[:2] == ['Demo', 'old_key']
        # 镜像已覆盖的 key 不追加
        assert 'enable' not in data

    def test_idempotent_second_run(self, i18n_files):
        # 第二次运行无新缺失，返回 0 且文件不再变化
        I18n.sync_missing_keys(MENU, fake_script_task)
        before = i18n_files.read_text(encoding='utf-8')
        assert I18n.sync_missing_keys(MENU, fake_script_task) == 0
        assert i18n_files.read_text(encoding='utf-8') == before

    def test_skip_when_mirror_missing(self, tmp_path, monkeypatch):
        # 镜像不存在（OASX 从未连接过）：跳过补齐，不创建/修改 assets 文件
        monkeypatch.setattr(I18n, 'file_zh_cn', tmp_path / 'no_mirror.json')
        assets_dir = tmp_path / 'assets'
        assets_dir.mkdir()
        monkeypatch.setattr(I18n, 'assets_i18n_dir', assets_dir)
        assert I18n.sync_missing_keys(MENU, fake_script_task) == 0
        assert not (assets_dir / 'zh-CN.json').exists()

    def test_skip_when_assets_broken(self, i18n_files):
        # assets JSON 损坏：不写回，保护人工翻译成果
        i18n_files.write_text('{broken json', encoding='utf-8')
        assert I18n.sync_missing_keys(MENU, fake_script_task) == 0
        assert i18n_files.read_text(encoding='utf-8') == '{broken json'
