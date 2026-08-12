import datetime
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
import requests
from typing import Generator, List, Tuple

from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager
from deploy.utils import DEPLOY_CONFIG
from module.logger import logger
from module.base.retry import retry
from module.server.config import DeployConfig


# git 自动升级：内置 git 不可用且本机也找不到可用 git 时，下载完整版 git 替换 toolkit/Git。
# 必须用完整版（.tar.bz2，Python 原生可解）：MinGit 裁剪掉了 git-remote-http.exe，
# 实测无法通过 https 拉取远程，装了也没用。
GIT_MIN_VERSION = (2, 30, 0)
GIT_UPGRADE_VERSION = '2.55.0.3'
# 国内源优先（淘宝 npmmirror / 华为云），GitHub 代理兜底，失败依次尝试下一个
GIT_UPGRADE_URLS = [
    'https://registry.npmmirror.com/-/binary/git-for-windows/v2.55.0.windows.3/Git-2.55.0.3-64-bit.tar.bz2',
    'https://mirrors.huaweicloud.com/git-for-windows/v2.55.0.windows.3/Git-2.55.0.3-64-bit.tar.bz2',
    'https://gh-proxy.com/https://github.com/git-for-windows/git/releases/download/'
    'v2.55.0.windows.3/Git-2.55.0.3-64-bit.tar.bz2',
]


class UpdateProgress:
    """线程安全的更新进度状态，供手动更新接口与前端轮询使用。

    status: idle / running / done / failed / rejected
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.status = 'idle'
        self.step = ''
        self.branch = ''
        self.logs = []
        self.finished = False

    def reset(self, branch):
        with self._lock:
            self.status = 'running'
            self.step = ''
            self.branch = branch
            self.logs = []
            self.finished = False

    def append(self, line):
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]

    def set_step(self, step):
        # 在锁内直接写 logs，避免嵌套加锁
        with self._lock:
            self.step = step
            self.logs.append(f'> {step}')
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]

    def finish(self, ok):
        # 在锁内直接写 logs，避免嵌套加锁（threading.Lock 不可重入）
        with self._lock:
            self.finished = True
            self.status = 'done' if ok else 'failed'
            self.logs.append('更新完成' if ok else '更新失败')
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]

    def reject(self, reason):
        # 在锁内直接写 logs，避免嵌套加锁
        with self._lock:
            self.finished = True
            self.status = 'rejected'
            self.logs.append(reason)
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]

    def snapshot(self):
        with self._lock:
            return {
                'status': self.status,
                'step': self.step,
                'branch': self.branch,
                'logs': list(self.logs),
                'finished': self.finished,
            }


_update_progress = UpdateProgress()


class Updater(DeployConfig, GitManager, PipManager):
    def __init__(self, file=DEPLOY_CONFIG):
        super().__init__(file=file)
        self.state = 0
        # fetch 成功与否，由 check_update 记录，供 /update_info 透出以区分「检查失败」与「无更新」
        self.fetch_ok = False

    @property
    def delay(self):
        self.read()
        return int(self.CheckUpdateInterval) * 60

    @property
    def schedule_time(self):
        self.read()
        t = self.AutoRestartTime
        if t is not None:
            return datetime.time.fromisoformat(t)
        else:
            return None

    def execute_output(self, command) -> str:
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding='utf-8',
            errors='replace',
            shell=True,
            creationflags=flags,
        )
        out, _ = proc.communicate()
        return out or ''

    def execute_stream(self, command, on_line=None) -> bool:
        """
        静默执行命令并逐行回调输出，返回进程是否成功（退出码 0）。

        不使用 os.system / subprocess.run(shell=True)：GUI(pythonw) 无控制台时
        会给 cmd 弹窗，CREATE_NO_WINDOW 让子进程无窗口静默运行。
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding='utf-8',
            errors='replace',
            shell=True,
            creationflags=flags,
        )
        for line in proc.stdout:
            line = line.rstrip('\n')
            logger.info(line)
            if on_line:
                on_line(line)
        return proc.wait() == 0

    @property
    def git_root(self) -> str:
        """GitExecutable 所在 git 安装根目录（绝对路径）。

        例如 toolkit/Git/mingw64/bin/git.exe -> .../toolkit/Git
        """
        if hasattr(self, '_git_root'):
            return self._git_root
        exe = self.git
        return os.path.dirname(os.path.dirname(os.path.dirname(exe)))

    @git_root.setter
    def git_root(self, value):
        # 允许注入/覆盖（测试或运行时指定 git 根目录）
        self._git_root = value

    @property
    def git_is_builtin(self) -> bool:
        """GitExecutable 是否指向项目内置 toolkit/Git（只有内置 git 才能自动升级）。"""
        if hasattr(self, '_git_is_builtin'):
            return self._git_is_builtin
        toolkit_dir = os.path.abspath('./toolkit').replace('\\', '/')
        return str(self.git_root).replace('\\', '/').startswith(toolkit_dir)

    @git_is_builtin.setter
    def git_is_builtin(self, value):
        self._git_is_builtin = value

    def _git_core_dir(self, exe) -> str:
        """定位 git 的 git-core 组件目录。

        优先问 git 自己（`--exec-path`，对 mingw64/bin 与 cmd 两种布局都准确），
        取不到有效目录时回退到按 git_root 推导。
        """
        try:
            exec_path = self.execute_output(f'"{exe}" --exec-path').strip()
        except Exception:
            exec_path = ''
        if exec_path and os.path.isdir(exec_path):
            return exec_path
        return os.path.join(self.git_root, 'mingw64', 'libexec', 'git-core')

    def _probe_git(self, exe):
        """
        离线检查指定 git.exe 是否具备 https 拉取能力。

        Args:
            exe: git.exe 路径

        Returns:
            tuple: (可用 bool, 原因 str)
        """
        try:
            version_text = self.execute_output(f'"{exe}" --version').strip()
        except Exception as e:
            return False, f'git 无法执行: {e}'
        m = re.search(r'(\d+)\.(\d+)\.(\d+)', version_text)
        if not m:
            return False, f'无法解析 git 版本: {version_text}'
        version = tuple(int(x) for x in m.groups())
        if version < GIT_MIN_VERSION:
            return False, f'git 版本过旧 ({version_text.strip()})，与 GitHub 协议不兼容'
        # 检查 https 传输组件，缺则必然无法拉取远程（MinGit 就缺这个）
        remote_http = os.path.join(self._git_core_dir(exe), 'git-remote-http.exe')
        if not os.path.exists(remote_http):
            return False, f'git 缺少 git-remote-http.exe，无法通过 https 拉取远程'
        return True, ''

    def check_git_usable(self):
        """
        离线检查当前 GitExecutable 是否具备 https 拉取能力。

        Returns:
            tuple: (可用 bool, 原因 str)
        """
        return self._probe_git(self.git)

    def use_git(self, exe) -> None:
        """
        切换 GitExecutable 到指定 git.exe 并落盘 deploy.yaml。

        `GitManager.git` 是 cached_property，改配置后必须失效缓存，
        否则同一实例仍会用切换前的旧路径。
        """
        self.GitExecutable = str(exe).replace('\\', '/')
        self.__dict__.pop('git', None)
        self.__dict__.pop('_git_root', None)

    def ensure_origin(self) -> bool:
        """确保 git origin 与 deploy.yaml 的 Repository 一致，不一致时自动切换。"""
        current = self.execute_output(f'"{self.git}" remote get-url origin').strip()
        if current == self.Repository:
            return True
        if current.startswith(('http://', 'https://', 'git@')):
            # origin 存在但地址不同，set-url 覆盖
            ok = self.execute_stream(f'"{self.git}" remote set-url origin {self.Repository}')
        else:
            # origin 不存在（get-url 输出报错文本），用 add 创建
            ok = self.execute_stream(f'"{self.git}" remote add origin {self.Repository}')
        if ok:
            logger.info(f'origin 已同步到 {self.Repository}')
            return True
        logger.warning('同步 origin 失败，更新可能仍走旧远程')
        return False

    def find_usable_git(self):
        """
        探测本机已安装的可用 git（含 https 传输组件），零下载优先。

        依次尝试 PATH 中的 git 与常见安装路径，返回第一个可用的 git.exe 路径，
        全部不可用时返回 None。

        Returns:
            str | None
        """
        candidates = []
        which = shutil.which('git')
        if which:
            candidates.append(which)
        for base in (
                os.environ.get('ProgramFiles', r'C:\Program Files'),
                os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                os.environ.get('LOCALAPPDATA', ''),
        ):
            if base:
                candidates.append(os.path.join(base, 'Git', 'cmd', 'git.exe'))
                candidates.append(os.path.join(base, 'Programs', 'Git', 'cmd', 'git.exe'))

        current = os.path.abspath(self.git).replace('\\', '/').lower()
        seen = set()
        for exe in candidates:
            exe = os.path.abspath(exe)
            key = exe.replace('\\', '/').lower()
            # 跳过重复候选与当前已判定不可用的 GitExecutable
            if key in seen or key == current:
                continue
            seen.add(key)
            if not os.path.exists(exe):
                continue
            usable, _ = self._probe_git(exe)
            if usable:
                return exe
        return None

    def upgrade_git(self, on_line=None) -> bool:
        """
        下载完整版 git 替换 toolkit/Git，失败自动回滚。返回是否成功。

        仅在内置 git 不可用、且本机也找不到可用 git 时作为兜底调用。

        Args:
            on_line: 进度回调（写入更新 log）
        """
        def emit(msg):
            if on_line:
                on_line(msg)
            else:
                logger.info(msg)

        if not self.git_is_builtin:
            emit('GitExecutable 不指向内置 toolkit/Git，无法自动升级，请手动配置可用 git')
            return False

        tmp_dir = tempfile.mkdtemp(prefix='oas_git_upgrade_')
        # 归档名沿用下载 URL 的后缀，_extract_archive 按后缀选解压方式
        archive_name = GIT_UPGRADE_URLS[0].rsplit('/', 1)[-1]
        zip_path = os.path.join(tmp_dir, archive_name)
        extract_dir = os.path.join(tmp_dir, 'git_new')
        try:
            # 1. 下载（多源依次尝试），进度节流后写入 log
            downloaded = False
            for url in GIT_UPGRADE_URLS:
                emit(f'下载完整版 git {GIT_UPGRADE_VERSION}（约 116MB，视网速可能需要几分钟）…')
                last_reported = 0

                def progress(d, t):
                    # 有总大小按 10% 一档、无总大小按每 10MB 一档，避免逐块刷屏
                    nonlocal last_reported
                    if t:
                        pct = d * 100 // t
                        if pct >= last_reported + 10 or d >= t:
                            last_reported = pct
                            emit(f'下载中: {d / 1048576:.1f} MB / {t / 1048576:.1f} MB ({pct}%)')
                    else:
                        mb = d // (10 * 1048576)
                        if mb > last_reported:
                            last_reported = mb
                            emit(f'下载中: {d / 1048576:.1f} MB')

                try:
                    self._download_git_archive(url, zip_path, on_progress=progress)
                    downloaded = True
                    break
                except Exception as e:
                    emit(f'下载失败({url}): {e}')
            if not downloaded:
                emit('所有下载源均失败')
                return False
            emit('下载完成')

            # 2. 解压（校验路径，防归档路径穿越）
            os.makedirs(extract_dir, exist_ok=True)
            emit('解压中（完整版体积较大，请耐心等待）…')
            self._extract_archive(zip_path, extract_dir)
            if not os.path.exists(os.path.join(extract_dir, 'mingw64', 'bin', 'git.exe')):
                emit('解压内容缺少 mingw64/bin/git.exe，结构异常')
                return False
            # 必须含 https 传输组件，否则替换了也拉不了远程（MinGit 就是这样）
            if not os.path.exists(os.path.join(
                    extract_dir, 'mingw64', 'libexec', 'git-core', 'git-remote-http.exe')):
                emit('解压内容缺少 git-remote-http.exe，该 git 无法通过 https 拉取，放弃替换')
                return False
            emit('解压完成')

            # 3. 备份 -> 替换 -> 验证，失败回滚
            git_root = self.git_root
            backup = git_root + '.bak'
            if os.path.exists(backup):
                shutil.rmtree(backup, ignore_errors=True)
            # 原 git 目录可能已被删除（如用户手动删掉 toolkit/Git 后走下载），此时无需备份
            if os.path.exists(git_root):
                shutil.move(git_root, backup)
            try:
                shutil.move(extract_dir, git_root)
            except Exception as e:
                self._restore_git(git_root, backup)
                emit(f'替换失败并已回滚: {e}')
                return False
            try:
                version_text = self.execute_output(f'"{self.git}" --version').strip()
                if not version_text:
                    raise Exception('git --version 无输出')
            except Exception as e:
                self._restore_git(git_root, backup)
                emit(f'新 git 验证失败并已回滚: {e}')
                return False
            # 验证通过，清理备份
            shutil.rmtree(backup, ignore_errors=True)
            emit(f'git 已升级到 {version_text}')
            return True
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _extract_archive(path, dest) -> None:
        """解压下载的 git 归档到 dest，拒绝含路径穿越的成员。

        完整版 git 发行包是 .tar.bz2（Python 原生可解，无需外部 7z）；
        同时兼容 .zip，便于切换下载源。
        """
        def unsafe(name):
            return ('..' in name.replace('\\', '/').split('/')
                    or name.startswith('/')
                    or (len(name) > 1 and name[1] == ':'))

        if path.endswith('.zip'):
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if unsafe(info.filename):
                        raise Exception(f'非法归档路径: {info.filename}')
                zf.extractall(dest)
            return
        with tarfile.open(path, 'r:bz2') as tf:
            for member in tf.getmembers():
                if unsafe(member.name):
                    raise Exception(f'非法归档路径: {member.name}')
            tf.extractall(dest)

    @staticmethod
    def _restore_git(git_root, backup):
        """替换失败时从备份恢复 toolkit/Git。"""
        if os.path.exists(git_root):
            shutil.rmtree(git_root, ignore_errors=True)
        if os.path.exists(backup):
            shutil.move(backup, git_root)

    def _download_git_archive(self, url, dest, on_progress=None) -> None:
        """流式下载 git 归档到 dest，失败抛异常。

        Args:
            on_progress: 进度回调(downloaded_bytes, total_bytes)，每个数据块调用一次
        """
        with requests.get(url, stream=True, timeout=(15, 180)) as r:
            r.raise_for_status()
            total = int(r.headers.get('Content-Length') or 0)
            downloaded = 0
            with open(dest, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)
        if total and downloaded < total:
            raise Exception(f'下载不完整: {downloaded}/{total} 字节')

    def get_commit(self, revision="", n=1, short_sha1=False) -> Tuple:
        """
        Return:
            (sha1, author, isotime, message,)
        """
        ph = "h" if short_sha1 else "H"

        log = self.execute_output(
            f'"{self.git}" log {revision} --pretty=format:"%{ph}---%an---%ad---%s" --date=iso -{n}'
        )

        if not log:
            return None, None, None, None

        # git 失败时 fatal 报错文本（stderr 被合并进 stdout）可能混入输出，且 commit
        # message 含换行时也会被拆成多行；只保留标准 4 字段行，避免把报错当 commit 返回
        logs = [
            tuple(line.split("---"))
            for line in log.split("\n")
            if len(line.split("---")) == 4
        ]
        if not logs:
            return None, None, None, None

        if n == 1:
            return logs[0]
        else:
            return logs

    def current_branch(self) -> str:
        return self.Branch

    def current_commit(self) -> str:
        return self.get_commit()

    def latest_commit(self) -> str:
        source = "origin"
        return self.get_commit(f"{source}/{self.Branch}")

    def check_update(self) -> bool:
        self.state = "checking"

        # if State.deploy_config.GitOverCdn:
        #     status = self.goc_client.get_status()
        #     if status == "uptodate":
        #         logger.info(f"No update")
        #         return False
        #     elif status == "behind":
        #         logger.info(f"New update available")
        #         return True
        #     else:
        #         # failed, should fallback to `git pull`
        #         pass

        # 确保 fetch 源与 deploy.yaml Repository 一致（用户直接改 yaml 时自动换源）
        self.fetch_ok = False
        if not self.ensure_origin():
            return False

        source = "origin"
        # fetch 加快速失败参数（TCP 3s、慢速 10s 中止），进程 25s 兜底 kill：
        # 连不上远程时尽快失败返回，避免 update_info 长时间挂起阻塞更新器页
        # fetch_ok 记录 fetch 是否成功，供 /update_info 区分「检查失败」与「无更新」
        self.fetch_ok = False
        for _ in range(2):
            if self.execute(
                    f'"{self.git}" -c http.connectTimeout=3 -c http.lowSpeedLimit=1000 '
                    f'-c http.lowSpeedTime=10 fetch {source} {self.Branch}',
                    allow_failure=True,
                    timeout=25,
            ):
                self.fetch_ok = True
                break
        else:
            logger.warning("Git fetch failed")
            return False

        log = self.execute_output(
            f'"{self.git}" log --not --remotes={source}/* -1 --oneline'
        )
        if log:
            logger.info(
                f"Cannot find local commit {log.split()[0]} in upstream, skip update"
            )
            return False

        sha1, _, _, message = self.get_commit(f"..{source}/{self.Branch}")

        if sha1:
            logger.info(f"New update available")
            logger.info(f"{sha1[:8]} - {message}")
            return True
        else:
            logger.info(f"No update")
            return False

    def execute_pull(self) -> bool:
        source = 'origin'
        prog = _update_progress
        prog.reset(branch=self.Branch)

        # 0. 检查 git 可用性；不可用时先找本机已装 git（零下载），再退到自动升级
        usable, reason = self.check_git_usable()
        if not usable:
            prog.set_step(f'git 不可用：{reason}')
            found = self.find_usable_git()
            if found:
                # 本机已有可用 git，改写 GitExecutable（DeployConfig 会落盘 deploy.yaml）
                prog.append(f'发现本机可用 git：{found}，切换使用')
                self.use_git(found)
                usable, reason = self.check_git_usable()
            if not usable:
                prog.set_step('未发现可用 git，尝试自动下载升级')
                if not self.upgrade_git(on_line=prog.append):
                    prog.reject(f'git 自动升级失败，无法继续更新：{reason}')
                    return False
                usable, reason = self.check_git_usable()
                if not usable:
                    prog.reject(f'git 升级后仍不可用：{reason}')
                    return False

        # 0.5 确保 fetch 源与 deploy.yaml Repository 一致（用户直接改 yaml 时自动换源）
        if not self.ensure_origin():
            prog.reject('同步 origin 失败，拒绝使用旧远程更新')
            return False

        # 1. fetch 目标分支（加快速失败参数，连不上远程时尽快失败，不让后台线程空挂）
        prog.set_step(f'fetch {source}/{self.Branch}')
        fetched = False
        for _ in range(3):
            if self.execute_stream(
                    f'"{self.git}" -c http.connectTimeout=3 -c http.lowSpeedLimit=1000 '
                    f'-c http.lowSpeedTime=10 fetch {source} {self.Branch}',
                    on_line=prog.append
            ):
                fetched = True
                break
        if not fetched:
            prog.finish(False)
            logger.warning('Git fetch failed')
            return False

        # 2. 若当前分支不是目标分支，先切换再更新
        current = self.execute_output(f'"{self.git}" symbolic-ref --short HEAD').strip()
        if current != self.Branch:
            prog.set_step(f'switch branch: {current} -> {self.Branch}')
            # 先校验未推送提交,避免丢弃本地改动后又因校验被拒绝
            unpushed = self.execute_output(
                f'"{self.git}" log --not --remotes={source}/* -1 --oneline').strip()
            if unpushed:
                prog.reject(f'本地存在未推送的提交：{unpushed}，拒绝切换分支。请先 push。')
                return False
            # 丢弃已跟踪文件改动,确保切换干净（不保留、不 stash、不拒绝）
            if not self.execute_stream(f'"{self.git}" diff --quiet HEAD'):
                prog.append('工作区有已跟踪文件的修改，直接丢弃后切换')
                if not self.execute_stream(f'"{self.git}" reset --hard HEAD', on_line=prog.append):
                    prog.finish(False)
                    logger.warning('Git reset --hard failed')
                    return False
            # 清理未跟踪文件/目录（忽略 .gitignore 保护项），
            # 避免与目标分支同名文件冲突导致 checkout 被覆盖拦截
            prog.append('清理未跟踪文件，确保切换不被覆盖冲突拦截')
            if not self.execute_stream(f'"{self.git}" clean -fd', on_line=prog.append):
                prog.finish(False)
                logger.warning('Git clean failed')
                return False
            # 切换：本地已有则直接 checkout，否则基于远程创建跟踪分支
            if self.execute_stream(f'"{self.git}" show-ref --verify refs/heads/{self.Branch}'):
                prog.set_step(f'git checkout {self.Branch}')
                switched = self.execute_stream(
                    f'"{self.git}" checkout {self.Branch}', on_line=prog.append
                )
            else:
                prog.set_step(f'git checkout -b {self.Branch} {source}/{self.Branch}')
                switched = self.execute_stream(
                    f'"{self.git}" checkout -b {self.Branch} {source}/{self.Branch}',
                    on_line=prog.append,
                )
            if not switched:
                prog.finish(False)
                logger.warning('Git checkout failed')
                return False

        # 3. pull 到最新
        prog.set_step(f'pull {source}/{self.Branch}')
        pulled = False
        for _ in range(3):
            if self.execute_stream(
                    f'"{self.git}" pull {source} {self.Branch} --no-rebase',
                    on_line=prog.append
            ):
                pulled = True
                break
        if not pulled:
            prog.finish(False)
            logger.warning('Git pull failed')
            return False

        prog.finish(True)
        logger.info('Update finished')
        return True



if __name__ == "__main__":
    updater = Updater()
    print(updater.latest_commit())



