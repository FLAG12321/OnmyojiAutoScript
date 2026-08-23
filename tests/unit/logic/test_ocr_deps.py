# This Python file uses the following encoding: utf-8
"""OCR 依赖一键对齐单元测试（deploy/ocr_deps.py）。

全部 mock pip 执行与已安装包查询，不联网、不真的装包，
因此可以在任意机器上作为门禁运行。

覆盖点：
1. plan() 纯逻辑：按 OcrDevice 决定 onnxruntime 发行版，识别需卸载的冲突包。
2. 幂等：已对齐时不产生任何 pip 动作。
3. Windows DLL 锁：当前进程已加载 onnxruntime 时拒绝换包。
4. 用户级包安全边界：只精确卸载 OCR 冲突包，不碰 frida / av / paramiko。
5. 损坏残留：换 ORT 发行版前清理 ~nnxruntime / ~-nxruntime 等 pip 临时目录，
   同时保护健康的正常安装不被误删。
"""
import sys

import pytest

from deploy.ocr_deps import (DIRECTML_DIST, ORT_VERSION, RAPIDOCR_VERSION,
                             USER_SITE_CONFLICTS, OcrDepsManager,
                             _is_ort_pip_temp, _sanitize_log, _ORT_INSTALL_RE)

pytestmark = pytest.mark.unit


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """构造 OcrDepsManager，隔离真实 deploy.yaml 与真实 pip。"""

    def build(installed=None, user_installed=None, models_ready=False, **overrides):
        deploy_file = tmp_path / 'deploy.yaml'
        lines = ['Deploy:', '  Ocr:']
        for key, value in overrides.items():
            lines.append(f'    {key}: {value}')
        deploy_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')

        mgr = OcrDepsManager(file=str(deploy_file))
        # 假装已安装包清单：{dist 名: 版本}
        mgr._installed = dict(installed or {})
        # 假装用户级已安装包清单
        mgr._user_installed = dict(user_installed or {})
        mgr._models_ready = models_ready
        monkeypatch.setattr(mgr, 'installed_versions', lambda: mgr._installed)
        monkeypatch.setattr(mgr, 'user_site_versions', lambda: mgr._user_installed)
        monkeypatch.setattr(mgr, 'models_ready', lambda: mgr._models_ready)
        # 单元测试默认不碰真实 site-packages；需要验证文件清理的用例单独覆盖
        monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [])
        # 记录所有 pip 命令而不真的执行
        mgr.commands = []
        monkeypatch.setattr(mgr, 'run_pip', lambda args, **kw: mgr.commands.append(args) or True)
        return mgr

    return build


def test_ort_install_regex_has_exact_distribution_whitelist():
    """只允许已知 ORT 发行版 metadata，不能匹配 onnxruntime_extensions。"""
    assert _ORT_INSTALL_RE.match('onnxruntime')
    assert _ORT_INSTALL_RE.match('onnxruntime-1.23.0.dist-info')
    assert _ORT_INSTALL_RE.match('onnxruntime_directml-1.23.0.dist-info')
    assert _ORT_INSTALL_RE.match('onnxruntime_gpu-1.23.0.dist-info')
    assert _ORT_INSTALL_RE.match('onnxruntime_openvino-1.23.0.dist-info')
    # 不能穷举旧白名单：这些真实 PyPI 发行版同样安装 onnxruntime 包目录。
    assert _ORT_INSTALL_RE.match('onnxruntime_qnn-1.23.0.dist-info')
    assert _ORT_INSTALL_RE.match('onnxruntime_training-1.23.0.dist-info')
    assert _ORT_INSTALL_RE.match('onnxruntime_azure-1.23.0.dist-info')
    assert not _ORT_INSTALL_RE.match('onnxruntime_extensions')
    assert not _ORT_INSTALL_RE.match('onnxruntime_extensions-1.0.dist-info')
    assert not _ORT_INSTALL_RE.match('onnxruntime-extensions-1.0.dist-info')


# ---------------- plan(): 发行版选择 ----------------

@pytest.mark.parametrize('device,expected', [
    ('auto', DIRECTML_DIST),
    ('dml', DIRECTML_DIST),
    ('cpu', 'onnxruntime'),
])
def test_required_ort_dist_follows_device(manager, device, expected):
    """auto / dml 用 directml 发行版（它同时提供 DML 与 CPU provider）。"""
    mgr = manager(OcrDevice=device)
    assert mgr.required_ort_dist() == expected


def test_plan_installs_ort_and_rapidocr_on_fresh_machine(manager):
    """全新机器（只有 v5 依赖）需要装 ORT 与 rapidocr。"""
    mgr = manager(installed={'onnxruntime': '1.16.3'}, OcrDevice='auto')
    plan = mgr.plan()
    assert f'{DIRECTML_DIST}=={ORT_VERSION}' in plan.install
    assert f'rapidocr=={RAPIDOCR_VERSION}' in plan.install
    assert 'onnxruntime' in plan.uninstall
    assert plan.needs_action is True


def test_plan_removes_wrong_ort_flavor(manager):
    """已装 CPU 版但配置要 GPU 时，必须先卸载 CPU 版再装 directml。

    两个发行版装的是同一个 onnxruntime 模块，共存会互相覆盖。
    """
    mgr = manager(installed={'onnxruntime': ORT_VERSION}, OcrDevice='dml')
    plan = mgr.plan()
    assert 'onnxruntime' in plan.uninstall
    assert f'{DIRECTML_DIST}=={ORT_VERSION}' in plan.install


def test_plan_removes_other_ort_flavor_when_cpu_forced(manager):
    mgr = manager(installed={DIRECTML_DIST: ORT_VERSION}, OcrDevice='cpu')
    plan = mgr.plan()
    assert DIRECTML_DIST in plan.uninstall
    assert f'onnxruntime=={ORT_VERSION}' in plan.install


def test_plan_removes_additional_ort_distribution_before_install(manager):
    # QNN 等共享 onnxruntime 包目录的发行版必须先卸载，避免 metadata 共存。
    mgr = manager(
        installed={'onnxruntime-qnn': ORT_VERSION},
        OcrDevice='auto',
    )
    plan = mgr.plan()
    assert 'onnxruntime-qnn' in plan.uninstall
    assert f'{DIRECTML_DIST}=={ORT_VERSION}' in plan.install


def test_plan_upgrades_wrong_ort_version(manager):
    """版本不对必须重装：1.16.3 无法加载 v6 的 IR v10 模型。"""
    mgr = manager(installed={DIRECTML_DIST: '1.16.3'}, OcrDevice='auto')
    plan = mgr.plan()
    assert f'{DIRECTML_DIST}=={ORT_VERSION}' in plan.install


def test_plan_is_idempotent_when_aligned(manager):
    """已对齐时不得产生任何 pip 动作，否则每次启动都重装一遍。"""
    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=True,
        OcrDevice='auto',
    )
    plan = mgr.plan()
    assert plan.install == []
    assert plan.uninstall == []
    assert plan.needs_models is False
    assert plan.needs_action is False


def test_plan_requests_models_when_missing(manager):
    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=False,
    )
    plan = mgr.plan()
    assert plan.needs_models is True
    assert plan.needs_action is True


def test_plan_uninstalls_v5_runtime_packages(manager):
    """v5 运行时包必须清掉，否则 requirements 会把 ORT 拉回 1.16.3。"""
    mgr = manager(installed={'ppocr-onnx': '0.0.3.9', 'onnxocr': '2025.5'})
    plan = mgr.plan()
    assert 'ppocr-onnx' in plan.uninstall
    assert 'onnxocr' in plan.uninstall


# ---------------- 用户级包安全边界 ----------------

def test_user_site_cleanup_only_targets_ocr_conflicts(manager):
    """只卸载真正属于 OCR 的用户级残留，其余包一律不动。

    opencv 变体刻意不在清单内：RapidOCR 只要求 cv2>=4.5.1.48，
    卸掉会把全项目 cv2 降到 toolkit 的 4.7.0.72，波及模板匹配。
    """
    mgr = manager(user_installed={
        'onnxocr': '2025.5',
        'opencv-contrib-python': '4.11.0.86',
        'opencv-python-headless': '4.11.0.86',
        'frida': '17.6.2',
        'frida-tools': '14.5.2',
        'av': '14.2.0',
        'paramiko': '5.0.0',
    })
    targets = mgr.plan().user_uninstall
    assert set(targets) == {'onnxocr'}
    for protected in ('frida', 'frida-tools', 'av', 'paramiko',
                      'opencv-contrib-python', 'opencv-python-headless'):
        assert protected not in targets


def test_protected_packages_never_in_conflict_list():
    """常量层面就把非 OCR 能力包排除，防止后续误扩清单。"""
    for protected in ('frida', 'frida-tools', 'av', 'paramiko', 'uvicorn',
                      'prompt-toolkit', 'bcrypt', 'pynacl',
                      'opencv-contrib-python', 'opencv-python-headless',
                      'opencv-python'):
        assert protected not in USER_SITE_CONFLICTS


def test_user_site_cleanup_skips_absent_packages(manager):
    mgr = manager(user_installed={'frida': '17.6.2'})
    assert mgr.plan().user_uninstall == []


# ---------------- 损坏的 ORT 残留 ----------------

# pip 的 AdjacentTempDirectory 生成规则（pip/_internal/utils/temp_dir.py）：
# `"~" + (i-1 个 LEADING_CHARS 字符) + 原名[i:]`，逐个 i 试到不冲突为止。
# 所以同一个 onnxruntime 会依次退化成 ~nnxruntime、~-nxruntime、~--xruntime……
# 真机上 ~nnxruntime 与 ~-nxruntime 同时存在过。
@pytest.mark.parametrize('name', [
    '~nnxruntime',                              # i=1，最常见
    '~-nxruntime',                              # i=2，真机实际出现过
    '~--xruntime',                              # i=3
    '~0nxruntime',                              # LEADING_CHARS 含数字
    '~nnxruntime-1.16.3.dist-info',             # dist-info 同样会被改名
    '~-nxruntime_directml-1.23.0.dist-info',
    '-nnxruntime',                              # 历史上见过的 `-` 前缀残留
])
def test_pip_temp_names_are_remnants(name):
    """pip 临时目录名必须被识别为残留。"""
    assert _is_ort_pip_temp(name) is True


@pytest.mark.parametrize('name', [
    'onnxruntime',                              # 正常包目录，删了就是数据损坏
    'onnxruntime-1.23.0.dist-info',             # 正常 CPU 版 metadata
    'onnxruntime_directml-1.23.0.dist-info',    # 正常 DirectML 版 metadata
    'onnxruntime_extensions',                   # 独立包，缺尾锚点时会被误删
    'rapidocr-3.9.2.dist-info',
    '~umpy',                                    # numpy 的 pip 残留，与 ORT 无关
    '~ich',                                     # rich 的 pip 残留
    '',
])
def test_healthy_and_unrelated_names_are_not_remnants(name):
    """正常命名与其它包的残留都不能被当成 ORT 残留。

    回归锚点：早先规则是 `^(?:[~_-]?onnxruntime|[~_-]nnxruntime)`，
    `[~_-]?` 的 `?` 让正常的 onnxruntime 目录也命中，而
    cleanup_ort_remnants 是无条件 rmtree —— 真机上把健康的
    onnxruntime-directml 1.23.0 删到 capi/DirectML.dll 才因 WinError 5 停住。
    同时缺尾锚点让 onnxruntime_extensions 这种独立包也在射程内。
    """
    assert _is_ort_pip_temp(name) is False


def test_cleanup_keeps_healthy_install_while_removing_temp_dirs(
        manager, tmp_path, monkeypatch):
    """真机场景：健康安装旁边有 pip 临时目录，只能删临时目录。

    这正是用户遇到的现场——~nnxruntime 让 has_ort_remnants 报 True，
    于是 align 调用清理，旧规则把健康的 onnxruntime/ 一起删了。
    """
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    # 一套健康的 onnxruntime-directml 1.23.0
    package_dir = site_dir / 'onnxruntime'
    (package_dir / 'capi').mkdir(parents=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / 'capi' / 'onnxruntime_pybind11_state.pyd').write_bytes(b'')
    (package_dir / 'capi' / 'DirectML.dll').write_bytes(b'')
    (site_dir / 'onnxruntime_directml-1.23.0.dist-info').mkdir()
    # 上一轮卸载中断留下的两个临时目录
    (site_dir / '~nnxruntime').mkdir()
    (site_dir / '~-nxruntime').mkdir()
    # 无关的独立包
    (site_dir / 'onnxruntime_extensions').mkdir()

    mgr = manager()
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])
    ok, err = mgr.cleanup_ort_remnants()

    assert (ok, err) == (True, '')
    assert sorted(p.name for p in site_dir.iterdir()) == [
        'onnxruntime',
        'onnxruntime_directml-1.23.0.dist-info',
        'onnxruntime_extensions',
    ]
    # 原生入口必须还在，否则 OCR 直接废掉
    assert (package_dir / 'capi' / 'DirectML.dll').is_file()
    # 清完临时目录后不该再报残留，否则 check() 永远红
    assert mgr.has_ort_remnants() is False


@pytest.mark.parametrize('dist_info', [
    'onnxruntime_qnn-1.23.0.dist-info',
    'onnxruntime_training-1.23.0.dist-info',
    'onnxruntime_azure-1.23.0.dist-info',
])
def test_cleanup_keeps_healthy_additional_ort_distributions(
        manager, tmp_path, monkeypatch, dist_info):
    """QNN/Training/Azure 的健康安装不能被误判，且不能删除包目录。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    package_dir = site_dir / 'onnxruntime'
    (package_dir / 'capi').mkdir(parents=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / 'capi' / 'onnxruntime_pybind11_state.pyd').write_bytes(b'')
    (site_dir / dist_info).mkdir()

    mgr = manager()
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])

    assert mgr.has_ort_remnants() is False
    assert mgr.cleanup_ort_remnants() == (True, '')
    assert package_dir.is_dir(), '健康的 onnxruntime 包目录不得被删除'
    assert (site_dir / dist_info).is_dir(), '健康发行版 metadata 不得被删除'


def test_cleanup_is_noop_for_fully_healthy_site_packages(
        manager, tmp_path, monkeypatch):
    """完全健康时清理必须什么都不删（align 换发行版时也会走到这里）。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    package_dir = site_dir / 'onnxruntime'
    (package_dir / 'capi').mkdir(parents=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / 'capi' / 'onnxruntime_pybind11_state.pyd').write_bytes(b'')
    (site_dir / 'onnxruntime_directml-1.23.0.dist-info').mkdir()

    mgr = manager()
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])
    before = sorted(p.name for p in site_dir.iterdir())
    ok, err = mgr.cleanup_ort_remnants()

    assert (ok, err) == (True, '')
    assert sorted(p.name for p in site_dir.iterdir()) == before


def test_cleanup_removes_orphan_package_without_dist_info(
        manager, tmp_path, monkeypatch):
    """包目录完整但 dist-info 没了：pip 不认它已安装，必须整套删掉重装。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    package_dir = site_dir / 'onnxruntime'
    (package_dir / 'capi').mkdir(parents=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / 'capi' / 'onnxruntime_pybind11_state.pyd').write_bytes(b'')

    mgr = manager()
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])
    assert mgr.has_ort_remnants() is True
    ok, err = mgr.cleanup_ort_remnants()

    assert (ok, err) == (True, '')
    assert list(site_dir.iterdir()) == []

def test_cleanup_removes_broken_ort_remnants(manager, tmp_path, monkeypatch):
    """清理残缺 onnxruntime 目录、非法 dist-info 与旧 directml metadata。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    (site_dir / 'onnxruntime').mkdir()
    (site_dir / 'onnxruntime' / '__init__.py').write_text('', encoding='utf-8')
    (site_dir / '~nnxruntime-1.16.3.dist-info').mkdir()
    (site_dir / 'onnxruntime_directml-1.23.0.dist-info').mkdir()
    (site_dir / 'rapidocr-3.9.2.dist-info').mkdir()

    mgr = manager()
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])
    ok, err = mgr.cleanup_ort_remnants()

    assert ok is True
    assert err == ''
    assert [p.name for p in site_dir.iterdir()] == ['rapidocr-3.9.2.dist-info']


def test_align_cleans_ort_remnants_before_pip(manager, monkeypatch):
    """换 ORT 发行版时必须先执行残留清理。"""
    mgr = manager(installed={'onnxruntime': '1.16.3'}, models_ready=True)
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    monkeypatch.setattr(mgr, 'prepare_models', lambda: (True, 'cpu'))
    calls = []
    monkeypatch.setattr(
        mgr,
        'cleanup_ort_remnants',
        lambda: calls.append(1) or (True, ''),
    )

    ok, _ = mgr.align()

    assert ok is True
    assert calls == [1]


def test_align_does_not_clean_healthy_ort(manager, monkeypatch):
    """已对齐时不能误删正常安装，避免每次启动都强制重装。"""
    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=True,
    )
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    calls = []
    monkeypatch.setattr(
        mgr,
        'cleanup_ort_remnants',
        lambda: calls.append(1) or (True, ''),
    )

    ok, _ = mgr.align()

    assert ok is True
    assert calls == []


def test_align_aborts_when_remnant_cleanup_fails(manager, monkeypatch):
    """残留清理失败时必须停止，不能继续走到残缺模块验证。"""
    mgr = manager(installed={'onnxruntime': '1.16.3'}, models_ready=True)
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    monkeypatch.setattr(mgr, 'cleanup_ort_remnants', lambda: (False, 'file is locked'))

    ok, reason = mgr.align()

    assert ok is False
    assert 'file is locked' in reason
    assert mgr.commands == []


def test_align_cleans_remnants_even_when_plan_aligned(manager, tmp_path, monkeypatch):
    """metadata 看似已对齐时，残缺包目录也必须触发清理重装。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    (site_dir / 'onnxruntime').mkdir()

    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=True,
    )
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])
    calls = []
    monkeypatch.setattr(
        mgr,
        'cleanup_ort_remnants',
        lambda: calls.append(1) or (True, ''),
    )

    ok, _ = mgr.align()

    assert ok is True
    assert calls == [1]


def test_check_reports_broken_ort_remnants(manager, tmp_path, monkeypatch):
    """--check 发现残留时不能误报已对齐。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    (site_dir / '~nnxruntime-1.16.3.dist-info').mkdir()

    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=True,
    )
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])

    assert mgr.check() is False


def test_check_ignores_healthy_ort_install(manager, tmp_path, monkeypatch):
    """正常 directml 安装不应被当成残留，避免每次检查都要求重装。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    package_dir = site_dir / 'onnxruntime'
    (package_dir / 'capi').mkdir(parents=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / 'capi' / 'onnxruntime_pybind11_state.pyd').write_bytes(b'')
    (site_dir / 'onnxruntime_directml-1.23.0.dist-info').mkdir()

    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=True,
    )
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])

    assert mgr.has_ort_remnants() is False
    assert mgr.check() is True


def test_check_detects_broken_package_with_valid_dist_info(manager, tmp_path, monkeypatch):
    """dist-info 存在但包目录缺少原生文件时仍算残留。"""
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    (site_dir / 'onnxruntime').mkdir()
    (site_dir / 'onnxruntime_directml-1.23.0.dist-info').mkdir()

    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=True,
    )
    monkeypatch.setattr(mgr, '_site_packages_dirs', lambda: [str(site_dir)])

    assert mgr.has_ort_remnants() is True
    assert mgr.check() is False


# ---------------- Windows DLL 锁 ----------------

def test_align_refuses_when_ort_loaded(manager, monkeypatch):
    """当前进程已 import onnxruntime 时拒绝换包。

    Windows 会锁定已加载的 onnxruntime.dll，此时 pip uninstall 会失败并
    留下 -nnxruntime-* 非法 distribution，环境进入半损坏状态。
    """
    mgr = manager(installed={'onnxruntime': '1.16.3'})
    monkeypatch.setitem(sys.modules, 'onnxruntime', object())
    ok, reason = mgr.align()
    assert ok is False
    assert 'onnxruntime' in reason
    assert mgr.commands == []


def test_align_proceeds_when_ort_not_loaded(manager, monkeypatch):
    mgr = manager(installed={'onnxruntime': '1.16.3'}, models_ready=True)
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    monkeypatch.setattr(mgr, 'prepare_models', lambda: (True, 'cpu'))
    ok, _ = mgr.align()
    assert ok is True
    assert mgr.commands, '应当执行了 pip 命令'


def test_align_skips_everything_when_disabled(manager, monkeypatch):
    """OcrAutoAlignDeps=false 时完全不动依赖，留给用户手动处理。"""
    mgr = manager(installed={'onnxruntime': '1.16.3'}, OcrAutoAlignDeps='false')
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    ok, reason = mgr.align()
    assert ok is True
    assert 'disabled' in reason.lower()
    assert mgr.commands == []


def test_align_noop_when_already_aligned(manager, monkeypatch):
    mgr = manager(
        installed={DIRECTML_DIST: ORT_VERSION, 'rapidocr': RAPIDOCR_VERSION},
        models_ready=True,
    )
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    ok, reason = mgr.align()
    assert ok is True
    assert mgr.commands == []
    assert 'aligned' in reason.lower()


# ---------------- 执行顺序 ----------------

def test_uninstall_runs_before_install(manager, monkeypatch):
    """必须先卸载冲突发行版再安装，否则两个 ORT 发行版会互相覆盖文件。"""
    mgr = manager(installed={'onnxruntime': '1.16.3', 'ppocr-onnx': '0.0.3.9'},
                  models_ready=True)
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    monkeypatch.setattr(mgr, 'prepare_models', lambda: (True, 'cpu'))
    mgr.align()
    joined = [' '.join(c) for c in mgr.commands]
    first_uninstall = next(i for i, c in enumerate(joined) if 'uninstall' in c)
    first_install = next(i for i, c in enumerate(joined) if 'install' in c and 'uninstall' not in c)
    assert first_uninstall < first_install


def test_install_command_targets_project_python(manager, monkeypatch):
    """pip 必须走项目内 python，绝不能落到系统 Python 或 user-site。"""
    mgr = manager(installed={}, models_ready=True)
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)
    monkeypatch.setattr(mgr, 'prepare_models', lambda: (True, 'cpu'))
    mgr.align()
    install_cmds = [c for c in mgr.commands if 'install' in c]
    assert install_cmds
    for cmd in install_cmds:
        assert '--user' not in cmd, 'pip 不得安装到用户级目录'


# ---------------- 子进程日志清洗 ----------------

def test_sanitize_strips_ansi_colors():
    """RapidOCR 日志带 ANSI 色码，转发到文件时是噪音。"""
    assert _sanitize_log('\x1b[32m[INFO] ready\x1b[0m') == '[INFO] ready'


def test_sanitize_replaces_undecodable_marker():
    """子进程按 errors='replace' 解码产生的 U+FFFD 在 GBK 控制台编不回去。

    logging 会自己吞掉 UnicodeEncodeError 并刷出一大段内部堆栈，
    掩盖真正的进度信息，所以必须在转发前替换掉。
    """
    assert '\ufffd' not in _sanitize_log('\ufffdT\ufffdT saved')


def test_sanitize_output_is_encodable_in_gbk():
    """清洗结果必须能被 GBK 编码，这是刷屏问题的根因判定。"""
    noisy = '\x1b[32m\ufffdT\ufffdT OCR ready \u2500\u2500\x1b[0m'
    cleaned = _sanitize_log(noisy)
    cleaned.encode('gbk')  # 不抛异常即通过


def test_sanitize_keeps_plain_chinese():
    """正常中文必须原样保留，不能被无差别降级成 ASCII。"""
    assert _sanitize_log('模型下载完成') == '模型下载完成'


def test_sanitize_keeps_ascii_paths():
    line = r'Successfully saved to: C:\proj\toolkit\ocr_models\det.onnx'
    assert _sanitize_log(line) == line
