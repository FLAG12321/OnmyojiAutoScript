# 更新器：切分支 + 静默执行 + 进度 + git 自动升级 单元测试
# 全部 mock git 输出，不触碰真实 config/deploy.yaml 与工作区。
import asyncio
import io
import os
import tarfile

import pytest

from module.server import home_router
from module.server.updater import Updater, _update_progress


@pytest.fixture
def updater(tmp_path):
    """用临时 deploy.yaml 构造 Updater，隔离真实 config/deploy.yaml。"""
    deploy_file = tmp_path / 'deploy.yaml'
    return Updater(file=str(deploy_file))


def reset_progress():
    _update_progress.reset('')


# 1. 当前分支 == 配置分支：只 pull，不 checkout
@pytest.mark.unit
def test_execute_pull_same_branch(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    calls = []
    updater.execute_output = lambda cmd: 'master\n'  # symbolic-ref 返回当前分支
    updater.execute_stream = lambda cmd, on_line=None: calls.append(cmd) or True
    assert updater.execute_pull() is True
    assert any('pull' in c for c in calls)
    assert not any('checkout' in c for c in calls)


# 2. 需切换，本地已有该分支：checkout + pull
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
    assert any('pull' in c for c in calls)


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


# 4. 已跟踪文件有修改：拒绝切换（rejected）
@pytest.mark.unit
def test_execute_pull_reject_dirty(updater):
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
            return False  # 有已跟踪修改
        return True

    updater.execute_stream = fake_stream
    assert updater.execute_pull() is False
    assert _update_progress.status == 'rejected'
    assert not any('checkout' in c for c in calls)


# 5. 本地存在未推送提交：拒绝切换（rejected）
@pytest.mark.unit
def test_execute_pull_reject_unpushed(updater):
    reset_progress()
    updater.check_git_usable = lambda: (True, '')
    updater.Branch = 'target'

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
    updater.execute_stream = lambda cmd, on_line=None: False
    assert updater.execute_pull() is False
    assert _update_progress.status == 'failed'


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

    def fail_download(url, dest):
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

    def fake_download(url, dest):
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

    def fake_download(url, dest):
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

    def fake_download(url, dest):
        with open(dest, 'wb') as f:
            f.write(payload)

    monkeypatch.setattr(updater, '_download_git_archive', fake_download)
    updater.execute_output = lambda cmd: 'git version 2.55.0.3.windows.1\n'

    logs = []
    assert updater.upgrade_git(on_line=logs.append) is True
    # 新 git 已就位，无备份残留
    assert (git_root / 'mingw64' / 'bin' / 'git.exe').exists()
    assert not os.path.exists(str(git_root) + '.bak')


# 18. execute_pull：git 不可用 → 自动升级 → 再 fetch/pull 成功
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
        return True  # fetch/checkout/pull 全成功

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
    assert any('pull' in c for c in calls)


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
