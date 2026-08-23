# 更新器：切分支 + 静默执行 + 进度 + git 自动升级 单元测试
# 除真实临时仓库回归外均 mock Git；所有用例都不触碰真实 deploy.yaml 与工作区。
import asyncio
import io
import os
import shutil
import subprocess
import tarfile
from filelock import FileLock

import pytest

from module.server import home_router
from module.server.updater import Updater, _update_progress


@pytest.fixture
def updater(tmp_path):
    """用临时 deploy.yaml 构造 Updater，隔离真实 deploy.yaml 与工作区。"""
    deploy_file = tmp_path / 'deploy.yaml'
    updater = Updater(file=str(deploy_file))
    # 隔离模板默认分支（模板 Branch 已改为 run_now_2），测试明确用 master 作为基线
    updater.Branch = 'master'
    # execute_pull 尾段的 align_ocr 会按 StartOcrServer 恢复 OCR 服务，
    # 单元测试不应真拉起子进程，显式关掉（模板默认值已是 true）
    updater.StartOcrServer = False
    # 必须同时关掉 OcrAutoAlignDeps：模板默认 true，否则 execute_pull 尾段会执行
    # 真实 align_ocr，进而调用 kill_orphan_ocr_servers() 对本机 taskkill ——
    # 跑一次测试就会把用户正在运行的 OCR 服务全部杀掉（实际踩过：任务报 LostRemote）。
    # 需要覆盖对齐逻辑的用例自己显式打开并 mock 掉进程操作。
    updater.OcrAutoAlignDeps = False
    return updater


def reset_progress():
    _update_progress.reset('')


# 1. 当前分支 == 配置分支：只快进，不 checkout
@pytest.mark.unit
def test_execute_pull_same_branch(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    calls = []
    updater.execute_output = lambda cmd: 'master\n'  # symbolic-ref 返回当前分支
    updater.execute_stream = lambda cmd, on_line=None: calls.append(cmd) or True
    assert updater.execute_pull() is True
    assert any('merge --ff-only origin/master' in c for c in calls)
    assert not any(' pull ' in c for c in calls)
    assert not any('checkout' in c for c in calls)


# 1b. 当前分支有本地修改：快进失败后清理并强制对齐远端
@pytest.mark.unit
def test_execute_pull_force_sync_dirty_same_branch(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.execute_output = lambda cmd: 'master\n'
    calls = []

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        # 模拟本地修改导致 merge --ff-only 输出 Git 覆盖保护错误并失败。
        return 'merge --ff-only' not in cmd

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is True
    assert _update_progress.status == 'done'
    assert any('clean -fd' in c for c in calls)
    assert any('reset --hard origin/master' in c for c in calls)
    assert not any(' pull ' in c for c in calls)


# 1c. 真实 Git 仓库回归：冲突源码被远端覆盖，忽略数据继续保留
@pytest.mark.unit
def test_execute_pull_real_git_force_sync(tmp_path, monkeypatch):
    git_exe = shutil.which('git')
    if not git_exe:
        pytest.skip('系统未安装 git，跳过真实仓库回归测试')

    remote = tmp_path / 'remote.git'
    seed = tmp_path / 'seed'
    client = tmp_path / 'client'

    def run_git(repo, *args):
        """在指定临时仓库执行 Git，并返回标准输出。"""
        result = subprocess.run(
            [git_exe, '-C', str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
        return result.stdout.strip()

    subprocess.run(
        [git_exe, 'init', '--bare', '--initial-branch=master', str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [git_exe, 'init', '--initial-branch=master', str(seed)],
        check=True,
        capture_output=True,
    )
    run_git(seed, 'config', 'user.name', 'Updater Test')
    run_git(seed, 'config', 'user.email', 'updater@example.invalid')
    (seed / 'assets' / 'i18n').mkdir(parents=True)
    (seed / 'assets' / 'i18n' / 'zh-CN.json').write_text('{"version": 1}\n', encoding='utf-8')
    (seed / 'Sidihon').write_text('version 1\n', encoding='utf-8')
    (seed / '.gitignore').write_text('runtime/\n', encoding='utf-8')
    run_git(seed, 'add', '.')
    run_git(seed, 'commit', '-m', 'initial')
    run_git(seed, 'remote', 'add', 'origin', str(remote))
    run_git(seed, 'push', '-u', 'origin', 'master')
    subprocess.run(
        [git_exe, 'clone', '--branch', 'master', str(remote), str(client)],
        check=True,
        capture_output=True,
    )

    # 远端推进两个会在安装机器上被本地修改阻塞的文件。
    (seed / 'assets' / 'i18n' / 'zh-CN.json').write_text('{"version": 2}\n', encoding='utf-8')
    (seed / 'Sidihon').write_text('version 2\n', encoding='utf-8')
    run_git(seed, 'commit', '-am', 'remote update')
    run_git(seed, 'push', 'origin', 'master')

    # 客户端制造同文件冲突、普通未跟踪文件以及应受 .gitignore 保护的数据。
    (client / 'assets' / 'i18n' / 'zh-CN.json').write_text('{"local": true}\n', encoding='utf-8')
    (client / 'Sidihon').write_text('local change\n', encoding='utf-8')
    (client / 'untracked.txt').write_text('remove me\n', encoding='utf-8')
    (client / 'runtime').mkdir()
    (client / 'runtime' / 'keep.json').write_text('{}\n', encoding='utf-8')

    real_updater = Updater(file=str(tmp_path / 'deploy.yaml'))
    real_updater.Branch = 'master'
    real_updater.GitExecutable = str(git_exe).replace('\\', '/')
    real_updater.KeepLocalChanges = False
    real_updater.OcrAutoAlignDeps = False
    real_updater.check_git_usable = lambda: (True, '')
    real_updater.ensure_origin = lambda: True
    monkeypatch.chdir(client)

    assert real_updater.execute_pull() is True
    assert (client / 'assets' / 'i18n' / 'zh-CN.json').read_text(encoding='utf-8').strip() == '{"version": 2}'
    assert (client / 'Sidihon').read_text(encoding='utf-8').strip() == 'version 2'
    assert not (client / 'untracked.txt').exists()
    assert (client / 'runtime' / 'keep.json').exists()
    assert run_git(client, 'rev-parse', 'HEAD') == run_git(client, 'rev-parse', 'origin/master')
    assert run_git(client, 'status', '--porcelain') == ''


# 1d. KeepLocalChanges=true 时快进失败不得清理本地文件
@pytest.mark.unit
def test_execute_pull_preserves_dirty_when_configured(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.KeepLocalChanges = True
    updater.execute_output = lambda cmd: 'master\n'
    calls = []

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        return 'merge --ff-only' not in cmd

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is False
    assert _update_progress.status == 'failed'
    assert not any('clean -fd' in c for c in calls)
    assert not any('reset --hard origin/master' in c for c in calls)


# 2. 需切换，本地已有该分支：checkout + 快进
@pytest.mark.unit
def test_execute_pull_switch_local(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'
    calls = []
    updater.execute_output = lambda cmd: 'old\n' if 'symbolic-ref' in cmd else ''

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        if 'fetch' in cmd:
            return True
        if 'diff --quiet' in cmd:
            return True  # 工作区干净
        if 'show-ref' in cmd:
            return True  # 本地分支已存在
        return True

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is True
    assert any('checkout target' in c and 'origin' not in c for c in calls)
    assert any('merge --ff-only origin/target' in c for c in calls)


# 3. 需切换，本地无该分支、远程有：checkout -b 创建跟踪分支
@pytest.mark.unit
def test_execute_pull_switch_new(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'
    calls = []
    updater.execute_output = lambda cmd: 'old\n' if 'symbolic-ref' in cmd else ''

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        if 'fetch' in cmd:
            return True
        if 'diff --quiet' in cmd:
            return True
        if 'show-ref' in cmd:
            return False  # 本地无此分支
        return True

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is True
    assert any('checkout -b target origin/target' in c for c in calls)


# 4. 已跟踪文件有修改：直接丢弃本地改动后切换
@pytest.mark.unit
def test_execute_pull_discards_dirty_then_switch(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'
    calls = []
    updater.execute_output = lambda cmd: 'old\n' if 'symbolic-ref' in cmd else ''

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        if 'diff --quiet' in cmd:
            return False  # 有已跟踪修改 → 触发 reset
        return True

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is True
    assert _update_progress.status == 'done'
    assert any('reset --hard' in c for c in calls)
    assert any('clean -fd' in c for c in calls)
    assert any('checkout target' in c and 'origin' not in c for c in calls)


# 4b. 有已跟踪修改但 reset 失败 → 更新失败
@pytest.mark.unit
def test_execute_pull_reset_fail(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'
    updater.execute_output = lambda cmd: 'old\n' if 'symbolic-ref' in cmd else ''

    def fake_stream(cmd, on_line=None):
        if 'diff --quiet' in cmd:
            return False  # 脏
        if 'reset --hard' in cmd:
            return False  # reset 失败
        return True

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is False


# 4c. 切换分支时 KeepLocalChanges=true：检测到脏文件后拒绝，不执行清理
@pytest.mark.unit
def test_execute_pull_switch_preserves_dirty_when_configured(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'
    updater.KeepLocalChanges = True
    updater.execute_output = lambda cmd: 'old\n' if 'symbolic-ref' in cmd else ''
    calls = []

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        return 'diff --quiet' not in cmd

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is False
    assert _update_progress.status == 'rejected'
    assert not any('reset --hard' in c for c in calls)
    assert not any('clean -fd' in c for c in calls)
    assert not any('checkout' in c for c in calls)


# 5. 配置保留本地改动时，未推送提交仍拒绝切换
@pytest.mark.unit
def test_execute_pull_reject_unpushed(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'
    updater.KeepLocalChanges = True

    def fake_output(cmd):
        if 'symbolic-ref' in cmd:
            return 'old\n'
        if 'log --not' in cmd:
            return 'abc123 commit message\n'
        return ''

    updater.execute_output = fake_output

    def fake_stream(cmd, on_line=None):
        if 'fetch' in cmd:
            return True
        if 'diff --quiet' in cmd:
            return True  # 工作区干净
        return True

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is False
    assert _update_progress.status == 'rejected'


# 6. 远程无该分支：checkout -b 失败 → failed
@pytest.mark.unit
def test_execute_pull_checkout_fail(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'
    updater.execute_output = lambda cmd: 'old\n' if 'symbolic-ref' in cmd else ''

    def fake_stream(cmd, on_line=None):
        if 'fetch' in cmd:
            return True
        if 'diff --quiet' in cmd:
            return True
        if 'show-ref' in cmd:
            return False
        if 'checkout' in cmd:
            return False  # 远程无此分支导致失败
        return True

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is False
    assert _update_progress.status == 'failed'


# 7. fetch 失败 → failed，进度含错误信息
@pytest.mark.unit
def test_execute_pull_fetch_fail(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.ensure_origin = lambda: True  # 本用例只验证 fetch 失败，不混入 origin 同步结果。
    updater.execute_stream = lambda cmd, on_line=None: False
    assert updater.execute_pull() is False
    assert _update_progress.status == 'failed'


# 7b. 失败点在 finish(False) 前必须写明「阶段 + 原因」，便于前端从 logs 定位中断点
@pytest.mark.unit
def test_execute_pull_fetch_fail_logs_stage(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.ensure_origin = lambda: True
    updater.execute_stream = lambda cmd, on_line=None: False
    updater.execute_pull()
    logs = _update_progress.snapshot()['logs']
    assert any('阶段「拉取远程代码」失败' in l for l in logs)


# 7c. OCR 依赖对齐是更新的收尾阶段：对齐失败则更新整体失败，
#     不能让「更新完成」在 OCR 尚未对齐/对齐失败时提前亮起
@pytest.mark.unit
def test_execute_pull_fails_when_ocr_align_fails(updater, monkeypatch):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.execute_output = lambda cmd: 'master\n'
    updater.execute_stream = lambda cmd, on_line=None: True
    monkeypatch.setattr(updater, 'align_ocr', lambda prog=None: False)
    assert updater.execute_pull() is False
    assert _update_progress.status == 'failed'


# 7d. 清理强杀 git 后残留的 .lock（fetch/merge 恢复的前提）
@pytest.mark.unit
def test_cleanup_git_locks(updater, tmp_path):
    git_dir = tmp_path / 'gitdir'
    refs = git_dir / 'refs' / 'heads'
    refs.mkdir(parents=True)
    (git_dir / 'index.lock').write_text('')
    (refs / 'master.lock').write_text('')
    (git_dir / 'config').write_text('not a lock')  # 非 .lock 文件必须保留
    updater.execute_output = lambda cmd: str(git_dir) if 'rev-parse' in cmd else ''
    updater.cleanup_git_locks()
    assert not (git_dir / 'index.lock').exists()
    assert not (refs / 'master.lock').exists()
    assert (git_dir / 'config').exists()


# 7e. 无 git 目录时清理应无副作用（rev-parse 失败/空目录）
@pytest.mark.unit
def test_cleanup_git_locks_noop_when_missing(updater):
    updater.execute_output = lambda cmd: ''
    updater.cleanup_git_locks()  # 不应抛异常


# 7f. OCR 对齐失败诊断：枚举到占用进程 → 提示里含进程与 PID
@pytest.mark.unit
def test_diagnose_ocr_blockers_finds_processes(updater, monkeypatch):
    class FakePM:
        def __init__(self, **kw):
            pass

        def iter_process_by_name(self, name):
            if name == 'python.exe':
                return iter([('D:/oas/toolkit/python.exe', 'python.exe', 1234)])
            return iter([])

    monkeypatch.setattr('deploy.process.ProcessManager', FakePM)
    msg = updater._diagnose_ocr_blockers()
    assert 'python.exe' in msg and '1234' in msg
    assert '请先全部停止' in msg


# 7g. 枚举正常但无占用进程 → 走「未检测到」分支
@pytest.mark.unit
def test_diagnose_ocr_blockers_none_found(updater, monkeypatch):
    class FakePM:
        def __init__(self, **kw):
            pass

        def iter_process_by_name(self, name):
            return iter([])

    monkeypatch.setattr('deploy.process.ProcessManager', FakePM)
    assert '未检测到' in updater._diagnose_ocr_blockers()


# 7h. pywin32 缺失（iter_process_by_name 返回 False）→ 「无法枚举」提示
@pytest.mark.unit
def test_diagnose_ocr_blockers_no_enumeration(updater, monkeypatch):
    class FakePM:
        def __init__(self, **kw):
            pass

        def iter_process_by_name(self, name):
            return False

    monkeypatch.setattr('deploy.process.ProcessManager', FakePM)
    assert '无法枚举' in updater._diagnose_ocr_blockers()


# 8. update_config 写 branch：deploy.yaml 实际落盘
@pytest.mark.unit
def test_update_config_branch(updater, monkeypatch, tmp_path):
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    result = asyncio.run(home_router.update_config(branch='dev'))
    assert result['branch'] == 'dev'
    content = (tmp_path / 'deploy.yaml').read_text(encoding='utf-8')
    assert 'dev' in content


# 9. update_config 写 repository：deploy.yaml 落盘 + 触发 git remote set-url
@pytest.mark.unit
def test_update_config_repository(updater, monkeypatch, tmp_path):
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    set_url_calls = []
    updater.execute_stream = lambda cmd, on_line=None: set_url_calls.append(cmd) or True
    result = asyncio.run(home_router.update_config(
        repository='https://example.com/repo.git'))
    assert result['repository'] == 'https://example.com/repo.git'
    content = (tmp_path / 'deploy.yaml').read_text(encoding='utf-8')
    assert 'https://example.com/repo.git' in content
    assert any('remote set-url origin' in c for c in set_url_calls)


# 10. update_config 校验非法 repository 格式
@pytest.mark.unit
def test_update_config_invalid_repository(updater, monkeypatch):
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    result = asyncio.run(home_router.update_config(repository='not-a-url'))
    assert 'error' in result


# 10b. update_config：Repository 与 deploy.yaml 相同也强制 set-url
#     （回归：之前相等的值会跳过 set-url，导致表单显示 gitee、实际拉取仍是 github）
@pytest.mark.unit
def test_update_config_same_repo_still_set_url(updater, monkeypatch):
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    updater.Repository = 'https://example.com/repo.git'
    set_url_calls = []
    updater.execute_stream = lambda cmd, on_line=None: set_url_calls.append(cmd) or True
    result = asyncio.run(home_router.update_config(
        repository='https://example.com/repo.git'))
    assert result['repository'] == 'https://example.com/repo.git'
    assert any('remote set-url origin' in c for c in set_url_calls)


# 10c. /execute_update：running 时拒绝再次触发（防重入，避免并发 git 互相踩锁）
@pytest.mark.unit
def test_execute_update_rejects_when_running(updater, monkeypatch):
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    reset_progress()  # status -> running
    started = []

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(home_router.threading, 'Thread', FakeThread)
    result = asyncio.run(home_router.execute_update())
    assert '运行' in result
    assert not started, 'running 时不应再启动更新线程'


# 10d. /execute_update：非 running（done/failed/idle）允许重试 —— 中断后一键恢复入口
@pytest.mark.unit
def test_execute_update_starts_thread_after_finished(updater, monkeypatch):
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    _update_progress.finish(False)  # status -> failed，模拟上次中断
    started = []

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(home_router.threading, 'Thread', FakeThread)
    result = asyncio.run(home_router.execute_update())
    assert '后台开始' in result
    assert started, '非 running 状态应允许再次触发更新'


@pytest.mark.unit
def test_execute_update_claim_is_atomic(updater, monkeypatch):
    """连续触发更新时，原子领取只允许一个后台线程启动。"""
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    started = []

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(home_router.threading, 'Thread', FakeThread)
    _update_progress.finish(True)
    first = asyncio.run(home_router.execute_update())
    second = asyncio.run(home_router.execute_update())
    assert '后台开始' in first
    assert '运行' in second
    assert len(started) == 1


@pytest.mark.unit
def test_align_ocr_restores_rpc_server_when_alignment_fails(updater, monkeypatch):
    """OCR 对齐失败时，已经停掉的 RPC 服务也必须恢复。"""
    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = True
    calls = []
    import sys
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)

    class FakeManager:
        python = './toolkit/python.exe'

        def check(self):
            return False

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())
    monkeypatch.setattr('module.ocr.rpc.shutdown_ocr_server',
                        lambda: calls.append('shutdown') or True)
    monkeypatch.setattr('module.ocr.rpc.kill_orphan_ocr_servers', lambda: 0)
    monkeypatch.setattr('module.ocr.rpc.ensure_ocr_server_started',
                        lambda: calls.append('start') or True)
    monkeypatch.setattr(updater, 'execute_stream', lambda *a, **k: False)

    assert updater.align_ocr() is False
    assert calls == ['shutdown', 'start']


@pytest.mark.unit
def test_update_config_rejects_invalid_direct_updater_values(updater):
    """Updater 配置赋值层也要保护独立更新器入口。"""
    with pytest.raises(ValueError, match='repository'):
        updater.Repository = 'https://example.com/repo.git;whoami'
    with pytest.raises(ValueError, match='branch'):
        updater.Branch = 'master;whoami'


@pytest.mark.unit
def test_execute_pull_rejects_when_cross_process_lock_is_held(updater, monkeypatch):
    """OASX 直接调用 execute_pull 时也必须和 Web 更新共享文件锁。"""
    # 本用例验证超时后的拒绝语义，不应真的等待生产配置的 30 秒。
    monkeypatch.setattr('module.server.updater.UPDATE_LOCK_WAIT', 0.01)
    reset_progress()
    lock_path = f'{os.path.abspath(updater.file)}.update.lock'
    with FileLock(lock_path):
        assert updater.execute_pull() is False
    assert _update_progress.status == 'rejected'


@pytest.mark.unit
def test_execute_pull_uses_nonzero_lock_timeout(updater, monkeypatch):
    """真实更新必须等待短暂的 --info fetch，而不是 timeout=0 立即拒绝。"""
    from contextlib import contextmanager
    import module.server.updater as updater_module

    captured = []

    @contextmanager
    def fake_lock(file, timeout=0):
        captured.append(timeout)
        yield

    monkeypatch.setattr(updater_module, 'update_lock', fake_lock)
    monkeypatch.setattr(updater, '_execute_pull_locked', lambda before_ocr=None: True)

    assert updater.execute_pull() is True
    assert captured == [updater_module.UPDATE_LOCK_WAIT]
    assert captured[0] >= 60, '等待时间必须覆盖两次 25 秒 fetch 的最坏情况'


@pytest.mark.unit
@pytest.mark.parametrize('repository', [
    'https://example.com/repo.git;whoami',
    'git@github.com:org/repo.git && whoami',
])
def test_update_config_rejects_repository_shell_injection(updater, monkeypatch, repository):
    """仓库地址含 shell 元字符时不得进入 git shell 命令。

    只断言「被拒且指明是 repository」，不锁具体错误文案：
    校验器改成逐字符报告非法字符后文案会变，锁文案会把这个用例变成文案门禁，
    而真正要守的是「危险输入进不去 shell」。
    """
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    result = asyncio.run(home_router.update_config(repository=repository))
    assert 'error' in result, 'shell 元字符必须被拒'
    assert 'repository' in result['error'], f'错误应指明是 repository：{result}'


@pytest.mark.unit
def test_update_config_rejects_branch_shell_injection(updater, monkeypatch):
    """分支名含命令分隔符时必须拒绝。"""
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    result = asyncio.run(home_router.update_config(branch='master;whoami'))
    assert 'error' in result, 'shell 元字符必须被拒'
    assert 'branch' in result['error'], f'错误应指明是 branch：{result}'


# 11. 进度 snapshot 字段齐全、状态流转正确
@pytest.mark.unit
def test_progress_snapshot():
    prog = _update_progress
    prog.reset('dev')
    prog.set_step('fetch origin/dev')
    prog.append('line1')
    snap = prog.snapshot()
    assert snap['status'] == 'running'
    assert snap['branch'] == 'dev'
    assert snap['finished'] is False
    assert any('fetch origin/dev' in l for l in snap['logs'])
    assert 'line1' in snap['logs']
    prog.finish(True)
    assert prog.snapshot()['status'] == 'done'
    prog.reset('')


# ---- git 自动升级相关 ----

# 12. check_git_usable：缺 git-remote-http.exe → 不可用
@pytest.mark.unit
def test_check_git_usable_missing_remote_http(updater, tmp_path):
    fake_git_root = tmp_path / 'Git'
    (fake_git_root / 'mingw64' / 'libexec' / 'git-core').mkdir(parents=True)
    updater.git_root = str(fake_git_root)
    updater.execute_output = lambda cmd: 'git version 2.48.0.windows.1\n'
    usable, reason = updater.check_git_usable()
    assert usable is False
    assert 'git-remote-http' in reason


# 13. check_git_usable：版本过旧 → 不可用
@pytest.mark.unit
def test_check_git_usable_old_version(updater, tmp_path):
    fake_git_root = tmp_path / 'Git'
    core = fake_git_root / 'mingw64' / 'libexec' / 'git-core'
    core.mkdir(parents=True)
    (core / 'git-remote-http.exe').touch()
    updater.git_root = str(fake_git_root)
    updater.execute_output = lambda cmd: 'git version 2.28.0.windows.1\n'
    usable, reason = updater.check_git_usable()
    assert usable is False
    assert '版本过旧' in reason


# 14. check_git_usable：版本够新且有传输组件 → 可用
@pytest.mark.unit
def test_check_git_usable_ok(updater, tmp_path):
    fake_git_root = tmp_path / 'Git'
    core = fake_git_root / 'mingw64' / 'libexec' / 'git-core'
    core.mkdir(parents=True)
    (core / 'git-remote-http.exe').touch()
    updater.git_root = str(fake_git_root)
    updater.execute_output = lambda cmd: 'git version 2.55.0.3.windows.1\n'
    usable, reason = updater.check_git_usable()
    assert usable is True


# 15. upgrade_git：GitExecutable 非内置 toolkit/Git → 拒绝
@pytest.mark.unit
def test_upgrade_git_not_builtin(updater, monkeypatch):
    monkeypatch.setattr(updater, 'git_is_builtin', False)
    assert updater.upgrade_git() is False


# 16. upgrade_git：下载失败 → False
@pytest.mark.unit
def test_upgrade_git_download_fail(updater, monkeypatch):
    monkeypatch.setattr(updater, 'git_is_builtin', True)

    def fail_download(url, dest, on_progress=None):
        raise Exception('network down')

    monkeypatch.setattr(updater, '_download_git_archive', fail_download)
    assert updater.upgrade_git() is False


def make_git_tarbz2(with_http=True) -> bytes:
    """构造一个 git 发行包结构的小 tar.bz2 作为下载产物。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:bz2') as tf:
        def add(name, content):
            data = content.encode('utf-8')
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        add('mingw64/bin/git.exe', 'new')
        if with_http:
            add('mingw64/libexec/git-core/git-remote-http.exe', 'http')
    return buf.getvalue()


# 17. upgrade_git：下载(tar.bz2)+解压+备份+替换+验证+清理备份
@pytest.mark.unit
def test_upgrade_git_replaces_and_verifies(updater, monkeypatch, tmp_path):
    git_root = tmp_path / 'Git'
    old_bin = git_root / 'mingw64' / 'bin'
    old_bin.mkdir(parents=True)
    (old_bin / 'git.exe').write_text('old', encoding='utf-8')
    monkeypatch.setattr(updater, 'git_root', str(git_root))
    monkeypatch.setattr(updater, 'git_is_builtin', True)

    payload = make_git_tarbz2()

    def fake_download(url, dest, on_progress=None):
        with open(dest, 'wb') as f:
            f.write(payload)

    monkeypatch.setattr(updater, '_download_git_archive', fake_download)
    updater.execute_output = lambda cmd: 'git version 2.55.0.3.windows.1\n'

    logs = []
    assert updater.upgrade_git(on_line=logs.append) is True
    # 新文件已替换，传输组件到位，备份已清理
    assert (git_root / 'mingw64' / 'bin' / 'git.exe').read_text(encoding='utf-8') == 'new'
    assert (git_root / 'mingw64' / 'libexec' / 'git-core' / 'git-remote-http.exe').exists()
    assert not os.path.exists(str(git_root) + '.bak')
    assert any('升级' in l for l in logs)


# 17b. upgrade_git：下载物缺 git-remote-http.exe（如 MinGit）→ 拒绝替换，旧 git 保留
@pytest.mark.unit
def test_upgrade_git_rejects_archive_without_http(updater, monkeypatch, tmp_path):
    git_root = tmp_path / 'Git'
    old_bin = git_root / 'mingw64' / 'bin'
    old_bin.mkdir(parents=True)
    (old_bin / 'git.exe').write_text('old', encoding='utf-8')
    monkeypatch.setattr(updater, 'git_root', str(git_root))
    monkeypatch.setattr(updater, 'git_is_builtin', True)

    payload = make_git_tarbz2(with_http=False)

    def fake_download(url, dest, on_progress=None):
        with open(dest, 'wb') as f:
            f.write(payload)

    monkeypatch.setattr(updater, '_download_git_archive', fake_download)
    logs = []
    assert updater.upgrade_git(on_line=logs.append) is False
    # 未替换，旧 git 原样保留
    assert (git_root / 'mingw64' / 'bin' / 'git.exe').read_text(encoding='utf-8') == 'old'
    assert any('git-remote-http.exe' in l for l in logs)


# 17c. _extract_archive：拒绝路径穿越成员
@pytest.mark.unit
def test_extract_archive_rejects_traversal(updater, tmp_path):
    archive = tmp_path / 'evil.tar.bz2'
    with tarfile.open(str(archive), 'w:bz2') as tf:
        data = b'x'
        info = tarfile.TarInfo('../evil.exe')
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    dest = tmp_path / 'out'
    dest.mkdir()
    with pytest.raises(Exception, match='非法归档路径'):
        updater._extract_archive(str(archive), str(dest))


# 17d. upgrade_git：原 toolkit/Git 目录已被删除（如用户手动删掉后走下载）
#     → 仍能正常下载替换，不因备份 FileNotFoundError 崩溃
@pytest.mark.unit
def test_upgrade_git_replaces_missing_root(updater, monkeypatch, tmp_path):
    git_root = tmp_path / 'Git'  # 目录不存在，模拟 toolkit/Git 已删除
    monkeypatch.setattr(updater, 'git_root', str(git_root))
    monkeypatch.setattr(updater, 'git_is_builtin', True)

    payload = make_git_tarbz2()

    def fake_download(url, dest, on_progress=None):
        with open(dest, 'wb') as f:
            f.write(payload)

    monkeypatch.setattr(updater, '_download_git_archive', fake_download)
    updater.execute_output = lambda cmd: 'git version 2.55.0.3.windows.1\n'

    logs = []
    assert updater.upgrade_git(on_line=logs.append) is True
    # 新 git 已就位，无备份残留
    assert (git_root / 'mingw64' / 'bin' / 'git.exe').exists()
    assert not os.path.exists(str(git_root) + '.bak')


# 17e. upgrade_git：下载进度回调写入 log（节流显示百分比/字节）
@pytest.mark.unit
def test_upgrade_git_reports_progress(updater, monkeypatch, tmp_path):
    git_root = tmp_path / 'Git'
    old_bin = git_root / 'mingw64' / 'bin'
    old_bin.mkdir(parents=True)
    (old_bin / 'git.exe').write_text('old', encoding='utf-8')
    monkeypatch.setattr(updater, 'git_root', str(git_root))
    monkeypatch.setattr(updater, 'git_is_builtin', True)

    payload = make_git_tarbz2()

    def fake_download(url, dest, on_progress=None):
        # 模拟下载：按比例回调进度（有总大小）
        if on_progress:
            total = len(payload)
            for frac in (0.25, 0.5, 0.75, 1.0):
                on_progress(int(total * frac), total)
        with open(dest, 'wb') as f:
            f.write(payload)

    monkeypatch.setattr(updater, '_download_git_archive', fake_download)
    updater.execute_output = lambda cmd: 'git version 2.55.0.3.windows.1\n'

    logs = []
    assert updater.upgrade_git(on_line=logs.append) is True
    # 进度行写入日志，且包含百分比与字节数
    progress_lines = [l for l in logs if '下载中' in l]
    assert progress_lines
    assert any('MB' in l and '%' in l for l in progress_lines)


# 18. execute_pull：git 不可用 → 自动升级 → 再 fetch/快进成功
@pytest.mark.unit
def test_execute_pull_upgrade_then_fetch(updater, monkeypatch, tmp_path):
    reset_progress()
    git_root = tmp_path / 'Git'
    core = git_root / 'mingw64' / 'libexec' / 'git-core'
    core.mkdir(parents=True)
    monkeypatch.setattr(updater, 'git_root', str(git_root))
    monkeypatch.setattr(updater, 'git_is_builtin', True)

    state = {'upgraded': False}

    def fake_output(cmd):
        if '--version' in cmd:
            # 升级前版本过旧，升级后版本够新
            return 'git version 2.55.0.3.windows.1\n' if state['upgraded'] else 'git version 2.28.0.windows.1\n'
        if 'symbolic-ref' in cmd:
            return 'master\n'
        return ''

    updater.execute_output = fake_output

    calls = []

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        return True  # fetch/checkout/快进全成功

    updater.execute_stream = fake_stream

    def fake_upgrade(on_line=None):
        state['upgraded'] = True
        (core / 'git-remote-http.exe').touch()  # 升级后具备传输组件
        return True

    monkeypatch.setattr(updater, 'upgrade_git', fake_upgrade)
    assert updater.execute_pull() is True
    assert state['upgraded'] is True
    assert any('fetch' in c for c in calls)


# ---- 复用本机已装 git（零下载优先） ----

def make_fake_git(tmp_path, name, with_http=True, version='2.48.1'):
    """在 tmp_path 下造一个 cmd/git.exe 布局的假 git，返回 exe 路径。"""
    root = tmp_path / name
    (root / 'cmd').mkdir(parents=True)
    exe = root / 'cmd' / 'git.exe'
    exe.touch()
    core = root / 'mingw64' / 'libexec' / 'git-core'
    core.mkdir(parents=True)
    if with_http:
        (core / 'git-remote-http.exe').touch()
    return str(exe), str(core)


# 19. _git_core_dir：优先采用 git --exec-path 的结果（不依赖路径层数推导）
@pytest.mark.unit
def test_git_core_dir_uses_exec_path(updater, tmp_path):
    exe, core = make_fake_git(tmp_path, 'SysGit')
    updater.git_root = str(tmp_path / 'wrong')  # 推导回退路径故意指向错误位置
    updater.execute_output = lambda cmd: core + '\n' if '--exec-path' in cmd else ''
    assert os.path.normpath(updater._git_core_dir(exe)) == os.path.normpath(core)


# 20. find_usable_git：PATH 中的 git 可用 → 返回该路径
@pytest.mark.unit
def test_find_usable_git_from_path(updater, tmp_path, monkeypatch):
    exe, core = make_fake_git(tmp_path, 'SysGit')
    monkeypatch.setattr('module.server.updater.shutil.which', lambda name: exe)

    def fake_output(cmd):
        if '--exec-path' in cmd:
            return core + '\n'
        if '--version' in cmd:
            return 'git version 2.48.1.windows.1\n'
        return ''

    updater.execute_output = fake_output
    assert os.path.normpath(updater.find_usable_git()) == os.path.normpath(exe)


# 21. find_usable_git：候选缺 http 传输组件 → None
@pytest.mark.unit
def test_find_usable_git_none_when_no_http(updater, tmp_path, monkeypatch):
    exe, core = make_fake_git(tmp_path, 'MinGit', with_http=False)
    monkeypatch.setattr('module.server.updater.shutil.which', lambda name: exe)

    def fake_output(cmd):
        if '--exec-path' in cmd:
            return core + '\n'
        if '--version' in cmd:
            return 'git version 2.55.0.3.windows.1\n'
        return ''

    updater.execute_output = fake_output
    assert updater.find_usable_git() is None


# 22. execute_pull：内置 git 不可用 → 复用本机 git，不触发下载升级
@pytest.mark.unit
def test_execute_pull_reuse_local_git(updater, monkeypatch, tmp_path):
    reset_progress()
    exe, core = make_fake_git(tmp_path, 'SysGit')
    upgraded = {'called': False}

    def fake_upgrade(on_line=None):
        upgraded['called'] = True
        return False

    monkeypatch.setattr(updater, 'upgrade_git', fake_upgrade)
    # 首次检查不可用，切到本机 git 后可用
    state = {'switched': False}
    updater.check_git_usable = lambda: (
        (True, '') if state['switched'] else (False, 'git 缺少 git-remote-http.exe，无法通过 https 拉取远程'))
    monkeypatch.setattr(updater, 'find_usable_git', lambda: exe)
    updater.execute_output = lambda cmd: 'master\n' if 'symbolic-ref' in cmd else ''

    calls = []

    def fake_stream(cmd, on_line=None):
        calls.append(cmd)
        return True

    updater.execute_stream = fake_stream

    # GitExecutable 被写入即视为切换成功
    original_setattr = type(updater).__setattr__

    def spy_setattr(self, key, value):
        if key == 'GitExecutable':
            state['switched'] = True
        original_setattr(self, key, value)

    monkeypatch.setattr(type(updater), '__setattr__', spy_setattr)
    assert updater.execute_pull() is True
    assert state['switched'] is True
    assert upgraded['called'] is False  # 零下载，未走升级
    assert any('merge --ff-only origin/master' in c for c in calls)


# 23. use_git：切换后必须失效 git 缓存（GitManager.git 是 cached_property）
@pytest.mark.unit
def test_use_git_invalidates_cache(updater, tmp_path):
    exe, _ = make_fake_git(tmp_path, 'SysGit')
    old = updater.git  # 触发 cached_property 缓存旧路径
    updater.use_git(exe)
    assert updater.git != old
    assert updater.git == str(exe).replace('\\', '/')


# 24. execute_pull：切换本机 git 后，未 mock 的 check_git_usable 必须转为可用
#     （回归：cached_property 未失效会导致切换后仍判不可用）
@pytest.mark.unit
def test_execute_pull_switch_makes_git_usable(updater, tmp_path, monkeypatch):
    reset_progress()
    exe, core = make_fake_git(tmp_path, 'SysGit')
    bad_exe, bad_core = make_fake_git(tmp_path, 'MinGit', with_http=False)
    updater.use_git(bad_exe)  # 起始为缺 http 的 git

    # use_git 会把路径统一成正斜杠，mock 里的匹配也要按同一形式比较
    good = str(exe).replace('\\', '/')

    def fake_output(cmd):
        if '--exec-path' in cmd:
            return (core if good in cmd.replace('\\', '/') else bad_core) + '\n'
        if '--version' in cmd:
            return 'git version 2.55.0.3.windows.1\n'
        if 'symbolic-ref' in cmd:
            return 'master\n'
        return ''

    updater.execute_output = fake_output
    monkeypatch.setattr(updater, 'find_usable_git', lambda: exe)

    def no_upgrade(on_line=None):
        raise AssertionError('本机已有可用 git，不应触发下载升级')

    monkeypatch.setattr(updater, 'upgrade_git', no_upgrade)
    updater.execute_stream = lambda cmd, on_line=None: True

    assert updater.execute_pull() is True
    assert updater.git == str(exe).replace('\\', '/')


# 25. ensure_origin：地址已与 deploy.yaml 一致时不执行 git 命令
@pytest.mark.unit
def test_ensure_origin_keeps_matching_url(updater):
    updater.Repository = 'https://example.com/repo.git'
    updater.execute_output = lambda cmd: 'https://example.com/repo.git\n'
    calls = []
    updater.execute_stream = lambda cmd, on_line=None: calls.append(cmd) or True
    assert updater.ensure_origin() is True
    assert calls == []


# 26. ensure_origin：origin 地址旧时自动 set-url 到 deploy.yaml Repository
@pytest.mark.unit
def test_ensure_origin_switches_url(updater):
    updater.Repository = 'https://gitee.com/example/repo.git'
    updater.execute_output = lambda cmd: 'https://github.com/example/repo.git\n'
    calls = []
    updater.execute_stream = lambda cmd, on_line=None: calls.append(cmd) or True
    assert updater.ensure_origin() is True
    assert calls == [
        f'"{updater.git}" remote set-url origin https://gitee.com/example/repo.git'
    ]


# 27. ensure_origin：origin 不存在时自动 add 到 deploy.yaml Repository
@pytest.mark.unit
def test_ensure_origin_adds_missing_remote(updater):
    updater.Repository = 'https://gitee.com/example/repo.git'
    updater.execute_output = lambda cmd: "error: No such remote 'origin'\n"
    calls = []
    updater.execute_stream = lambda cmd, on_line=None: calls.append(cmd) or True
    assert updater.ensure_origin() is True
    assert calls == [
        f'"{updater.git}" remote add origin https://gitee.com/example/repo.git'
    ]


# 28. check_update：origin 同步失败时不执行 fetch，fetch_ok 保持 false
@pytest.mark.unit
def test_check_update_stops_when_ensure_origin_fails(updater, monkeypatch):
    monkeypatch.setattr(updater, 'ensure_origin', lambda: False)

    def unexpected_fetch(*args, **kwargs):
        raise AssertionError('origin 同步失败后不应继续 fetch')

    updater.execute = unexpected_fetch
    assert updater.check_update() is False
    assert updater.fetch_ok is False


# 28b. 未推送提交仅在保留模式阻止更新；安装模式仍应显示远端更新
@pytest.mark.unit
def test_check_update_unpushed_respects_keep_local_changes(updater, monkeypatch):
    monkeypatch.setattr(updater, 'ensure_origin', lambda: True)
    updater.execute = lambda cmd, allow_failure=False, output=True, timeout=None: True
    updater.execute_output = lambda cmd: 'abc123 local commit\n'
    updater.get_commit = lambda revision='', n=1, short_sha1=False: (
        'def456', 'Updater', '2026-08-18 00:00:00 +0800', 'remote update'
    )

    updater.KeepLocalChanges = False
    assert updater.check_update() is True
    updater.KeepLocalChanges = True
    assert updater.check_update() is False


# 29. execute_pull：origin 同步失败时拒绝更新，不执行 fetch/pull
@pytest.mark.unit
def test_execute_pull_rejects_when_ensure_origin_fails(updater, monkeypatch):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    monkeypatch.setattr(updater, 'ensure_origin', lambda: False)

    def unexpected_stream(*args, **kwargs):
        raise AssertionError('origin 同步失败后不应继续 fetch/pull')

    updater.execute_stream = unexpected_stream
    assert updater.execute_pull() is False
    assert _update_progress.status == 'rejected'


# 30. get_commit：git 报错文本（fatal，如远程引用缺失）→ 返回空元组，不把报错当 commit
@pytest.mark.unit
def test_get_commit_filters_git_error(updater):
    updater.execute_output = lambda cmd: (
        "fatal: ambiguous argument 'origin/master': unknown revision or path not in the working tree.\n"
        "Use '--' to separate paths from revisions, like this:\n"
        "'git <command> [<revision>...] -- [<file>...]'\n"
    )
    assert updater.get_commit('origin/master') == (None, None, None, None)
    # n>1 无合法行时同样返回空元组，避免把脏数据交给前端
    assert updater.get_commit('origin/master', n=15) == (None, None, None, None)


# 26. get_commit：标准 4 字段输出 → 正常解析；杂行混入时只保留合法行
@pytest.mark.unit
def test_get_commit_parses_valid(updater):
    updater.execute_output = lambda cmd: (
        'abc123---Alice---2026-01-01 00:00:00 +0800---fix bug\n'
    )
    assert updater.get_commit() == ('abc123', 'Alice', '2026-01-01 00:00:00 +0800', 'fix bug')
    updater.execute_output = lambda cmd: (
        "fatal: ambiguous argument 'origin/master': unknown revision or path not in the working tree.\n"
        'abc123---Alice---2026-01-01 00:00:00 +0800---fix bug\n'
    )
    assert updater.get_commit('origin/master', n=15) == [
        ('abc123', 'Alice', '2026-01-01 00:00:00 +0800', 'fix bug')
    ]


# 27. check_update：fetch 失败 → fetch_ok=False；fetch 成功但无更新 → fetch_ok=True
@pytest.mark.unit
def test_check_update_fetch_ok(updater, monkeypatch):
    monkeypatch.setattr(updater, 'ensure_origin', lambda: True)
    # fetch 全部失败（连不上远程）
    updater.execute = lambda cmd, allow_failure=False, output=True, timeout=None: False
    assert updater.check_update() is False
    assert updater.fetch_ok is False

    # fetch 成功、无新提交 → is_update=False 但 fetch_ok=True
    updater.execute = lambda cmd, allow_failure=False, output=True, timeout=None: True
    updater.execute_output = lambda cmd: ''   # git log 无输出 → 本地无领先、远程无差异
    assert updater.check_update() is False
    assert updater.fetch_ok is True


# 28. /update_info 透出 fetch_ok，前端据此区分「检查失败」与「无更新」
@pytest.mark.unit
def test_update_info_returns_fetch_ok(updater, monkeypatch):
    monkeypatch.setattr(home_router, 'Updater', lambda: updater)
    updater.fetch_ok = False
    updater.check_update = lambda: False
    updater.current_branch = lambda: 'master'
    updater.Repository = 'https://example.com/repo.git'
    updater.current_commit = lambda: ('a' * 40, 'u', 't', 'm')
    updater.latest_commit = lambda: ('a' * 40, 'u', 't', 'm')
    updater.get_commit = lambda n=15: [('a' * 40, 'u', 't', 'm')]
    result = home_router.update_info()
    assert result['fetch_ok'] is False
    # fetch 成功后 fetch_ok 应透出 True
    updater.fetch_ok = True
    result = home_router.update_info()
    assert result['fetch_ok'] is True


# 29. align_ocr：依赖已对齐时不停止正常 RPC，只确认服务按配置运行。
@pytest.mark.unit
def test_align_ocr_restarts_ocr_server_when_enabled(updater, monkeypatch):
    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = True
    calls = {'shutdown': 0, 'start': 0}

    class FakeManager:
        def check(self):
            return True

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())

    def fake_shutdown(*a, **k):
        calls['shutdown'] += 1
        return True

    def fake_start(*a, **k):
        calls['start'] += 1
        return True

    monkeypatch.setattr('module.ocr.rpc.shutdown_ocr_server', fake_shutdown)
    monkeypatch.setattr('module.ocr.rpc.kill_orphan_ocr_servers', lambda: 0)
    monkeypatch.setattr('module.ocr.rpc.ensure_ocr_server_started', fake_start)

    assert updater.align_ocr() is True
    assert calls['shutdown'] == 0, '依赖已对齐时不得无故停止正常 RPC 服务'
    assert calls['start'] == 1, '依赖已对齐时仍应确认 RPC 服务按配置运行'


@pytest.mark.unit
def test_align_ocr_fails_when_rpc_restore_fails(updater, monkeypatch):
    """依赖已对齐但配置要求的 RPC 无法启动时，更新不得报告成功。"""
    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = True

    class FakeManager:
        def check(self):
            return True

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())
    monkeypatch.setattr('module.ocr.rpc.ensure_ocr_server_started', lambda: False)

    assert updater.align_ocr() is False


@pytest.mark.unit
def test_align_ocr_does_not_start_server_when_disabled(updater, monkeypatch):
    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = False
    calls = {'start': 0}

    class FakeManager:
        def check(self):
            return True

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())
    monkeypatch.setattr('module.ocr.rpc.shutdown_ocr_server',
                        lambda *a, **k: True)
    monkeypatch.setattr('module.ocr.rpc.kill_orphan_ocr_servers', lambda: 0)

    def fake_start(*a, **k):
        calls['start'] += 1
        return True

    monkeypatch.setattr('module.ocr.rpc.ensure_ocr_server_started', fake_start)

    assert updater.align_ocr() is True
    assert calls['start'] == 0


@pytest.mark.unit
def test_align_ocr_returns_false_on_deps_failure(updater, monkeypatch):
    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = False

    class FakeManager:
        def check(self):
            return False

        python = './toolkit/python.exe'

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())
    monkeypatch.setattr('module.ocr.rpc.shutdown_ocr_server',
                        lambda *a, **k: True)
    monkeypatch.setattr('module.ocr.rpc.kill_orphan_ocr_servers', lambda: 0)
    monkeypatch.setattr(updater, 'execute_stream',
                        lambda cmd, on_line=None: False)

    assert updater.align_ocr() is False


@pytest.mark.unit
def test_align_ocr_kills_orphan_ocr_servers(updater, monkeypatch):
    """对齐前必须终止外部持有的 OCR 服务进程，才能释放 onnxruntime.dll。

    shutdown_ocr_server 只停本进程拉起的子进程；多开/残留场景下 OCR 服务
    由别的进程持有，kill_orphan_ocr_servers 负责把这些外部进程一并终止。
    """
    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = False
    calls = {'kill': 0}
    import sys
    monkeypatch.delitem(sys.modules, 'onnxruntime', raising=False)

    class FakeManager:
        python = './toolkit/python.exe'

        def check(self):
            return False

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())
    monkeypatch.setattr('module.ocr.rpc.shutdown_ocr_server', lambda *a, **k: False)
    monkeypatch.setattr(updater, 'execute_stream', lambda *a, **k: True)

    def fake_kill():
        calls['kill'] += 1
        return 1

    monkeypatch.setattr('module.ocr.rpc.kill_orphan_ocr_servers', fake_kill)
    monkeypatch.setattr('module.ocr.rpc.ensure_ocr_server_started', lambda: True)

    assert updater.align_ocr() is True
    assert calls['kill'] == 1, '对齐前必须终止外部 OCR 服务进程以释放 onnxruntime.dll'


# ---- 独立更新器（deploy/update.py）相关 ----

# 34. align_ocr：需要换包但本进程已加载 onnxruntime → 拒绝换包并给出出路
@pytest.mark.unit
def test_align_ocr_refuses_swap_when_ort_loaded_in_process(updater, monkeypatch):
    """本进程持有 ORT 时不得发起注定失败的 pip 换包。

    Windows 锁定已加载的 onnxruntime_providers_shared.dll，且 Python 无法卸载
    已加载的扩展 DLL。server/gui/script 入口都会 preload ORT，此时换包必然
    WinError 5 并留下半损坏 distribution，只能引导用户走独立更新器。
    """
    import sys as _sys

    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = False
    streamed = []

    class FakeManager:
        def check(self):
            return False  # 需要换包

        python = './toolkit/python.exe'

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())
    monkeypatch.setattr('module.ocr.rpc.shutdown_ocr_server', lambda *a, **k: True)
    monkeypatch.setattr('module.ocr.rpc.kill_orphan_ocr_servers', lambda: 0)
    monkeypatch.setattr(updater, 'execute_stream',
                        lambda cmd, on_line=None: streamed.append(cmd) or True)
    # 模拟 server 进程：onnxruntime 已在 sys.modules 里
    monkeypatch.setitem(_sys.modules, 'onnxruntime', object())

    reset_progress()
    assert updater.align_ocr(_update_progress) is False
    assert not any('deploy.ocr_deps' in c for c in streamed), \
        '本进程持有 ORT 时不得启动 ocr_deps 换包，那必然失败并损坏依赖'
    logs = '\n'.join(_update_progress.snapshot()['logs'])
    assert 'oas-update.bat' in logs, '必须指出独立更新器这条出路'


# 35. align_ocr：本进程已加载 ORT 但依赖本已对齐 → 不受守卫影响，正常通过
@pytest.mark.unit
def test_align_ocr_ort_loaded_but_already_aligned_still_ok(updater, monkeypatch):
    """守卫只在真正需要换包时生效，不得把原本成功的更新变成失败。"""
    import sys as _sys

    updater.OcrAutoAlignDeps = True
    updater.StartOcrServer = False

    class FakeManager:
        def check(self):
            return True  # 无需变更

    monkeypatch.setattr('deploy.ocr_deps.OcrDepsManager', lambda file: FakeManager())
    monkeypatch.setattr('module.ocr.rpc.shutdown_ocr_server', lambda *a, **k: True)
    monkeypatch.setattr('module.ocr.rpc.kill_orphan_ocr_servers', lambda: 0)
    monkeypatch.setitem(_sys.modules, 'onnxruntime', object())

    assert updater.align_ocr() is True


# 36. execute_pull：before_ocr 钩子在 OCR 对齐之前执行，失败则整体失败
@pytest.mark.unit
def test_execute_pull_runs_before_ocr_hook_ahead_of_align(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.execute_output = lambda cmd: 'master\n'
    updater.execute_stream = lambda cmd, on_line=None: True
    order = []
    updater.align_ocr = lambda prog=None: order.append('ocr') or True

    assert updater.execute_pull(before_ocr=lambda prog: order.append('pip') or True) is True
    assert order == ['pip', 'ocr'], 'pip 依赖必须在 OCR 对齐之前对齐'


@pytest.mark.unit
def test_execute_pull_fails_when_before_ocr_fails(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.execute_output = lambda cmd: 'master\n'
    updater.execute_stream = lambda cmd, on_line=None: True
    aligned = []
    updater.align_ocr = lambda prog=None: aligned.append(1) or True

    assert updater.execute_pull(before_ocr=lambda prog: False) is False
    assert _update_progress.status == 'failed'
    assert not aligned, 'pip 阶段失败后不应继续 OCR 对齐'


# 37. execute_pull：不传 before_ocr 时行为不变（web 路径零回归）
@pytest.mark.unit
def test_execute_pull_without_hook_is_unchanged(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.execute_output = lambda cmd: 'master\n'
    updater.execute_stream = lambda cmd, on_line=None: True
    updater.align_ocr = lambda prog=None: True

    assert updater.execute_pull() is True
    assert _update_progress.status == 'done'


# 38. UpdateProgress 监听器：阶段/日志/结束都实时转发，取消后不再收到
@pytest.mark.unit
def test_progress_listener_forwards_lines():
    prog = _update_progress
    seen = []
    prog.reset('dev')
    prog.set_listener(seen.append)
    prog.set_step('fetch origin/dev')
    prog.append('line1')
    prog.finish(True)
    prog.set_listener(None)
    prog.append('after unset')

    assert '> fetch origin/dev' in seen
    assert 'line1' in seen
    assert '更新完成' in seen
    assert 'after unset' not in seen, '取消监听后不得继续转发'
    prog.reset('')


@pytest.mark.unit
def test_progress_listener_exception_does_not_break_update():
    """监听器抛异常不得影响更新流程本身。"""
    prog = _update_progress
    prog.reset('dev')

    def boom(line):
        raise RuntimeError('listener boom')

    prog.set_listener(boom)
    prog.append('still recorded')
    prog.set_listener(None)
    assert 'still recorded' in prog.snapshot()['logs']
    prog.reset('')
