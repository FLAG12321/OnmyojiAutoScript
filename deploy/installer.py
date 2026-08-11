from deploy.patch import pre_checks

pre_checks()

from deploy.adb import AdbManager
from deploy.process import ProcessManager
from deploy.fluentui import FluentuiManager
from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager


class Installer(GitManager, PipManager, AdbManager, FluentuiManager, ProcessManager):
    def install(self):
        try:
            # 先确保内置 toolkit git 可用（不可用则下载完整版替换），再 git_install 拉代码
            self.ensure_git_ready()
            self.git_install()
            self.process_kill()
            self.pip_install()
            self.adb_install()
        except ExecutionError:
            exit(1)


if __name__ == '__main__':
    Installer().install()
