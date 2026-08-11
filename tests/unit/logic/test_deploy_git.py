# 安装阶段 git 准备：ensure_git_ready / download_git_full 单元测试
# 全部 mock 下载与 git 输出，不触碰真实 toolkit/Git 与网络。
import io
import os
import tarfile

import pytest

from deploy.git import GitManager


@pytest.fixture
def gm(tmp_path):
    """用临时 deploy.yaml 构造 GitManager，隔离真实 config/deploy.yaml。"""
    deploy_file = tmp_path / 'deploy.yaml'
    deploy_file.write_text('Deploy:\n  Git:\n    Branch: master\n', encoding='utf-8')
    return GitManager(file=str(deploy_file))


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


def fake_git_version(monkeypatch, version_text='git version 2.55.0.3.windows.1\n'):
    """mock subprocess.Popen，让 _check_git_ready 返回指定 --version 输出。"""
    class FakeProc:
        def communicate(self, timeout=None):
            return version_text, None

    monkeypatch.setattr('deploy.git.subprocess.Popen',
                        lambda *a, **k: FakeProc())


# 1. _check_git_ready：缺 git-remote-http.exe → 不可用
@pytest.mark.unit
def test_check_git_ready_missing_http(monkeypatch, tmp_path):
    fake_git_version(monkeypatch)  # 版本够新
    git_root = tmp_path / 'Git'
    core = git_root / 'mingw64' / 'libexec' / 'git-core'
    core.mkdir(parents=True)
    exe = git_root / 'mingw64' / 'bin' / 'git.exe'
    exe.parent.mkdir(parents=True)
    exe.touch()
    usable, reason = GitManager._check_git_ready(str(exe), str(git_root))
    assert usable is False
    assert 'git-remote-http' in reason


# 2. _check_git_ready：版本过旧 → 不可用
@pytest.mark.unit
def test_check_git_ready_old_version(monkeypatch, tmp_path):
    fake_git_version(monkeypatch, 'git version 2.28.0.windows.1\n')
    git_root = tmp_path / 'Git'
    core = git_root / 'mingw64' / 'libexec' / 'git-core'
    core.mkdir(parents=True)
    (core / 'git-remote-http.exe').touch()
    exe = git_root / 'mingw64' / 'bin' / 'git.exe'
    exe.parent.mkdir(parents=True)
    exe.touch()
    usable, reason = GitManager._check_git_ready(str(exe), str(git_root))
    assert usable is False
    assert '版本过旧' in reason


# 2b. _check_git_ready：版本够新且有传输组件 → 可用
@pytest.mark.unit
def test_check_git_ready_ok(monkeypatch, tmp_path):
    fake_git_version(monkeypatch)
    git_root = tmp_path / 'Git'
    core = git_root / 'mingw64' / 'libexec' / 'git-core'
    core.mkdir(parents=True)
    (core / 'git-remote-http.exe').touch()
    exe = git_root / 'mingw64' / 'bin' / 'git.exe'
    exe.parent.mkdir(parents=True)
    exe.touch()
    usable, reason = GitManager._check_git_ready(str(exe), str(git_root))
    assert usable is True


# 3. download_git_full：下载(tar.bz2)+解压+替换+验证+清理备份
@pytest.mark.unit
def test_download_git_full_replaces(gm, monkeypatch, tmp_path):
    git_root = tmp_path / 'Git'
    old_bin = git_root / 'mingw64' / 'bin'
    old_bin.mkdir(parents=True)
    (old_bin / 'git.exe').write_text('old', encoding='utf-8')

    payload = make_git_tarbz2()

    def fake_download(url, dest, on_progress=None):
        if on_progress:
            on_progress(len(payload), len(payload))
        with open(dest, 'wb') as f:
            f.write(payload)

    monkeypatch.setattr(gm, '_download_archive', fake_download)
    gm.execute_output = lambda cmd: 'git version 2.55.0.3.windows.1\n'

    logs = []
    assert gm.download_git_full(str(git_root), on_line=logs.append) is True
    assert (git_root / 'mingw64' / 'bin' / 'git.exe').read_text(encoding='utf-8') == 'new'
    assert (git_root / 'mingw64' / 'libexec' / 'git-core' / 'git-remote-http.exe').exists()
    assert not os.path.exists(str(git_root) + '.bak')
    assert any('下载中' in l for l in logs)
    assert any('升级' in l for l in logs)


# 4. download_git_full：下载物缺 git-remote-http.exe → 拒绝替换，旧 git 保留
@pytest.mark.unit
def test_download_git_full_rejects_no_http(gm, monkeypatch, tmp_path):
    git_root = tmp_path / 'Git'
    old_bin = git_root / 'mingw64' / 'bin'
    old_bin.mkdir(parents=True)
    (old_bin / 'git.exe').write_text('old', encoding='utf-8')

    payload = make_git_tarbz2(with_http=False)

    def fake_download(url, dest, on_progress=None):
        with open(dest, 'wb') as f:
            f.write(payload)

    monkeypatch.setattr(gm, '_download_archive', fake_download)
    logs = []
    assert gm.download_git_full(str(git_root), on_line=logs.append) is False
    assert (git_root / 'mingw64' / 'bin' / 'git.exe').read_text(encoding='utf-8') == 'old'
    assert any('git-remote-http.exe' in l for l in logs)


# 5. ensure_git_ready：内置 git 已可用 → 不触发下载
@pytest.mark.unit
def test_ensure_git_ready_already_usable(gm, monkeypatch):
    # _check_git_ready 是 @staticmethod，替换时需用 staticmethod 包一层避免实例绑定
    monkeypatch.setattr(GitManager, '_check_git_ready',
                        staticmethod(lambda exe, git_root: (True, '')))

    def no_download(git_root, on_line=None):
        raise AssertionError('git 已可用，不应触发下载')

    monkeypatch.setattr(gm, 'download_git_full', no_download)
    assert gm.ensure_git_ready() is True


# 6. ensure_git_ready：内置 git 不可用 → 下载替换后可用
@pytest.mark.unit
def test_ensure_git_ready_downloads_when_unusable(gm, monkeypatch):
    state = {'replaced': False}

    def fake_check(exe, git_root):
        # 下载前不可用，下载后可用
        return (True, '') if state['replaced'] else (False, 'git 缺少 git-remote-http.exe')

    monkeypatch.setattr(GitManager, '_check_git_ready', staticmethod(fake_check))
    downloads = []

    def fake_download(git_root, on_line=None):
        downloads.append(git_root)
        state['replaced'] = True
        return True

    monkeypatch.setattr(gm, 'download_git_full', fake_download)
    assert gm.ensure_git_ready() is True
    assert len(downloads) == 1
    assert 'toolkit' in downloads[0].replace('\\', '/')  # 固定检查 ./toolkit/Git
