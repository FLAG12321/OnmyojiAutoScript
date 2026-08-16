# This Python file uses the following encoding: utf-8
"""OCR 依赖与模型一键对齐。

目标：任意机器上执行一次就能把 OCR 环境从 v5 切到 PP-OCRv6，
并按 deploy 配置装好正确的 onnxruntime 发行版、下载好模型。

为什么不能只靠 requirements.txt：

1. `onnxruntime` 与 `onnxruntime-directml` 是两个发行版名，但装的是同一个
   onnxruntime 模块。pip 不认为后者满足前者的依赖声明，只要 requirements
   里还有任何包依赖 `onnxruntime`，pip 就会把 GPU 版覆盖成 CPU 版。
2. GPU 与否是机器相关的运行期决定，requirements 是静态文件，表达不了。
3. Windows 会锁定已加载的 onnxruntime.dll，换包必须在没有推理进程时做，
   这需要显式的时机控制。

因此 ORT 发行版由本模块独占管理，requirements.txt 不再声明 onnxruntime。

用法：
    ./toolkit/python.exe -m deploy.ocr_deps           # 对齐
    ./toolkit/python.exe -m deploy.ocr_deps --check   # 只检查，不改动
"""
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from deploy.config import DeployConfig
from deploy.logger import logger
from deploy.utils import DEPLOY_CONFIG

# onnxruntime 版本下限由模型格式决定：RapidOCR 3.9.2 的模型是 ONNX IR v10，
# 1.16.3 只支持到 IR v9，会直接报 Unsupported model IR version
ORT_VERSION = '1.23.0'
RAPIDOCR_VERSION = '3.9.2'
# DirectML 发行版同时提供 DmlExecutionProvider 与 CPUExecutionProvider，
# 因此 auto / dml 都装它：一个包覆盖有卡和无卡两种机器
DIRECTML_DIST = 'onnxruntime-directml'
CPU_DIST = 'onnxruntime'
# 所有已知的 onnxruntime 发行版，同一时间只允许存在一个
ORT_DISTS = (CPU_DIST, DIRECTML_DIST, 'onnxruntime-gpu', 'onnxruntime-openvino')

# v5 运行时包。留着会把 onnxruntime 依赖拉回 1.16.3，必须卸掉
V5_DISTS = ('ppocr-onnx', 'onnxocr')

# 用户级（%APPDATA%\Python）里会遮蔽项目内包的 OCR 冲突包。
# 只列真正属于 OCR 的残留：frida / frida-tools / av / paramiko 等只存在于
# 用户级，是其它功能的唯一来源，误卸会直接废掉那些功能。
#
# 刻意不含 opencv 变体：RapidOCR 只要求 cv2>=4.5.1.48，用户级的 4.11.0 满足，
# 卸掉反而会把全项目的 cv2 换成 toolkit 的 4.7.0.72，波及所有模板匹配。
# 那属于独立的依赖治理，不该由 OCR 升级顺带改变。
USER_SITE_CONFLICTS = (
    'onnxocr',
)

# 模型目录里必须存在的文件名片段，用于判断模型是否已下载
MODEL_MARKERS = ('det', 'rec')

# ANSI 色码。RapidOCR 的日志带颜色，转发到文件或 GBK 控制台时是噪音
_ANSI_PATTERN = re.compile(r'\x1b\[[0-9;]*m')


def _sanitize_log(line: str) -> str:
    """把子进程输出清洗成当前控制台一定能写出的字符串。

    子进程输出用 errors='replace' 解码，无法识别的字节会变成 U+FFFD，
    而 U+FFFD 在 GBK 控制台上又编不回去。logging 内部会捕获
    UnicodeEncodeError 并打印一大段 "--- Logging error ---" 堆栈，
    既刷屏又掩盖真正的进度信息，所以必须在转发前处理掉。

    同时去掉 ANSI 色码：RapidOCR 的日志带颜色，重定向到文件时是噪音。
    """
    line = _ANSI_PATTERN.sub('', line).replace('�', '?')
    encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        line.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        # 仍有控制台放不下的字符（如日志里的框线符），整体降级到 ASCII
        line = line.encode('ascii', 'replace').decode('ascii')
    return line


def _safe_log(line: str) -> None:
    """转发子进程输出，保证不会因编码问题刷出 logging 内部错误堆栈。"""
    logger.info(_sanitize_log(line))


@dataclass
class OcrDepsPlan:
    """一次对齐要做的事情。"""

    uninstall: List[str] = field(default_factory=list)
    install: List[str] = field(default_factory=list)
    user_uninstall: List[str] = field(default_factory=list)
    needs_models: bool = False

    @property
    def needs_action(self) -> bool:
        return bool(self.uninstall or self.install or self.user_uninstall or self.needs_models)

    def describe(self) -> str:
        parts = []
        if self.uninstall:
            parts.append(f'uninstall={self.uninstall}')
        if self.install:
            parts.append(f'install={self.install}')
        if self.user_uninstall:
            parts.append(f'user-uninstall={self.user_uninstall}')
        if self.needs_models:
            parts.append('download-models')
        return ', '.join(parts) if parts else 'nothing to do'


class OcrDepsManager(DeployConfig):
    """按部署配置对齐 OCR 依赖与模型。"""

    def __init__(self, file=DEPLOY_CONFIG):
        super().__init__(file=file)

    # ---------------- 环境查询 ----------------

    @property
    def python(self) -> str:
        return self.filepath('PythonExecutable')

    @property
    def model_dir(self) -> str:
        return self.filepath('OcrModelDir')

    def installed_versions(self) -> Dict[str, str]:
        """当前解释器可见的已安装包 {规范化名: 版本}。"""
        import importlib.metadata as md

        found = {}
        for dist in md.distributions():
            name = dist.metadata['Name']
            if not name:
                continue
            found[self._normalize(name)] = dist.version
        return found

    def user_site_versions(self) -> Dict[str, str]:
        """用户级 site-packages 里的已安装包。

        用户级目录在 sys.path 中排在项目目录之前，会遮蔽项目内的包。
        """
        import site

        user_site = site.getusersitepackages()
        if not os.path.isdir(user_site):
            return {}

        found = {}
        for entry in os.listdir(user_site):
            if not entry.endswith('.dist-info'):
                continue
            name = entry[:-len('.dist-info')].rsplit('-', 1)[0]
            found[self._normalize(name)] = entry
        return found

    def models_ready(self) -> bool:
        """模型目录里是否已有 det 与 rec 的 onnx 文件。"""
        directory = self.model_dir
        if not os.path.isdir(directory):
            return False
        files = [f.lower() for f in os.listdir(directory) if f.lower().endswith('.onnx')]
        return all(any(marker in f for f in files) for marker in MODEL_MARKERS)

    @staticmethod
    def _normalize(name: str) -> str:
        """PEP 503 规范化包名：下划线与点都按连字符比较。"""
        return name.lower().replace('_', '-').replace('.', '-')

    # ---------------- 计划 ----------------

    def required_ort_dist(self) -> str:
        """按 OcrDevice 决定应安装哪个 onnxruntime 发行版。"""
        return CPU_DIST if str(self.OcrDevice) == 'cpu' else DIRECTML_DIST

    def plan(self) -> OcrDepsPlan:
        """算出需要执行的卸载/安装/下载动作（纯逻辑，不产生副作用）。"""
        installed = self.installed_versions()
        wanted_dist = self.required_ort_dist()
        result = OcrDepsPlan()

        # 1. 清掉其它 onnxruntime 发行版：它们装的是同一个模块，共存会互相覆盖
        for dist in ORT_DISTS:
            if dist != wanted_dist and dist in installed:
                result.uninstall.append(dist)

        # 2. v5 运行时包：留着会把 onnxruntime 拉回 1.16.3
        for dist in V5_DISTS:
            if dist in installed:
                result.uninstall.append(dist)

        # 3. 目标 ORT 发行版与版本
        if installed.get(wanted_dist) != ORT_VERSION:
            result.install.append(f'{wanted_dist}=={ORT_VERSION}')

        # 4. rapidocr
        if installed.get('rapidocr') != RAPIDOCR_VERSION:
            result.install.append(f'rapidocr=={RAPIDOCR_VERSION}')

        # 5. 用户级冲突包：只动明确列出的 OCR 相关项
        user_installed = self.user_site_versions()
        result.user_uninstall = [name for name in USER_SITE_CONFLICTS
                                 if name in user_installed]

        # 6. 模型
        result.needs_models = not self.models_ready()
        return result

    # ---------------- 执行 ----------------

    def pip_args(self) -> List[str]:
        """镜像与 SSL 相关的 pip 公共参数，复用 deploy 配置。"""
        args = ['--disable-pip-version-check']
        if self.PypiMirror:
            args += ['-i', self.PypiMirror]
            if 'http:' in self.PypiMirror or not self.SSLVerify:
                host = urlparse(self.PypiMirror).hostname
                if host:
                    args += ['--trusted-host', host]
        elif not self.SSLVerify:
            args += ['--trusted-host', 'pypi.org', '--trusted-host', 'files.pythonhosted.org']
        return args

    def run_pip(self, args: List[str], allow_failure: bool = False) -> bool:
        """执行一条 pip 命令。

        用 subprocess 而不是 os.system：GUI(pythonw) 无控制台时
        CREATE_NO_WINDOW 才能静默运行，不弹 CMD 窗口。
        """
        command = [self.python, '-m', 'pip'] + args
        logger.info(' '.join(command))
        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding='utf-8',
            errors='replace',
            creationflags=flags,
        )
        for line in proc.stdout:
            _safe_log(line.rstrip('\n'))
        code = proc.wait()
        if code and not allow_failure:
            logger.warning(f'pip failed with code {code}: {" ".join(args)}')
            return False
        return True

    def prepare_models(self) -> Tuple[bool, str]:
        """在子进程里构造一次 v6 模型，触发模型下载并验证真实可推理。

        必须开子进程：本进程一旦 import onnxruntime，后续换包就会被 DLL 锁挡住。
        同时这一步也顺带确定了本机最终使用的设备（GPU 还是 CPU）。

        Returns:
            tuple: (是否成功, 解析出的设备名或错误信息)
        """
        script = (
            'import numpy as np;'
            'from module.ocr.models import get_local_ocr_model;'
            'm = get_local_ocr_model("ch");'
            'm.ocr_single_line(np.zeros((32, 64, 3), dtype=np.uint8));'
            'print("OCR_DEVICE=" + m.resolved_device);'
            'print("OCR_MODEL_TYPE=" + m.resolved_model_type)'
        )
        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
        proc = subprocess.Popen(
            [self.python, '-c', script],
            cwd=self.root_filepath,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding='utf-8',
            errors='replace',
            creationflags=flags,
        )
        device = ''
        lines = []
        for line in proc.stdout:
            line = line.rstrip('\n')
            lines.append(line)
            _safe_log(line)
            if line.startswith('OCR_DEVICE='):
                device = line.split('=', 1)[1].strip()
        if proc.wait() or not device:
            return False, '\n'.join(lines[-10:])
        return True, device

    def align(self) -> Tuple[bool, str]:
        """对齐 OCR 依赖与模型。幂等，可在每次安装/更新时调用。

        Returns:
            tuple: (是否成功, 说明信息)
        """
        logger.hr('Align OCR dependencies', 1)

        if not self.OcrAutoAlignDeps:
            logger.info('OcrAutoAlignDeps is disabled, skip')
            return True, 'OcrAutoAlignDeps disabled'

        # Windows 下已加载的 onnxruntime.dll 被锁定，此时 pip uninstall 会失败
        # 并留下 -nnxruntime-* 非法 distribution，把环境搞成半损坏状态
        if 'onnxruntime' in sys.modules:
            reason = ('onnxruntime is already loaded in this process, '
                      'refuse to swap packages (Windows locks the DLL)')
            logger.warning(reason)
            return False, reason

        plan = self.plan()
        logger.info(f'OCR deps plan: {plan.describe()}')
        if not plan.needs_action:
            return True, 'OCR dependencies already aligned'

        common = self.pip_args()

        # 顺序很重要：先卸载冲突发行版，再安装目标版本。
        # 反过来会让两个 onnxruntime 发行版互相覆盖同名文件。
        for dist in plan.uninstall:
            self.run_pip(['uninstall', '-y', dist] + ['--disable-pip-version-check'],
                         allow_failure=True)
        for dist in plan.user_uninstall:
            logger.info(f'Remove user-site package shadowing project: {dist}')
            self.run_pip(['uninstall', '-y', dist] + ['--disable-pip-version-check'],
                         allow_failure=True)

        if plan.install:
            if not self.run_pip(['install'] + plan.install + common):
                return False, f'pip install failed: {plan.install}'

        ok, info = self.prepare_models()
        if not ok:
            return False, f'OCR model preparation failed: {info}'

        logger.info(f'OCR ready: PP-OCRv6 {self.OcrModelType} on {info}')
        return True, f'aligned, device={info}'

    def check(self) -> bool:
        """只检查不改动，返回 True 表示已对齐。"""
        plan = self.plan()
        logger.info(f'OCR deps status: {plan.describe()}')
        return not plan.needs_action


def main() -> int:
    check_only = '--check' in sys.argv
    manager = OcrDepsManager()
    if check_only:
        return 0 if manager.check() else 1
    ok, info = manager.align()
    logger.info(info)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
