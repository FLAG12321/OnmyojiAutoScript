# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import atexit
import gc
import os
import pickle
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
import zerorpc

from module.exception import ScriptError
from module.logger import logger
from module.ocr.result import BoxedResult

# 服务进程句柄。用 subprocess 而不是 multiprocessing：
# Windows spawn 会 re-import 本模块，而本模块顶层就 import zerorpc(gevent)，
# 那样 onnxruntime 的 DLL 初始化必然失败。必须用独立入口 module.ocr.server_boot
# 保证 onnxruntime 先于 gevent 加载，详见 server_boot.py 的说明。
_OCR_SERVER_PROCESS: Optional[subprocess.Popen] = None
_OCR_CONTROL_CLIENT = None
_OCR_CONTROL_ADDRESS: Optional[str] = None
_OCR_CONTROL_LOCK = threading.Lock()


def _normalize_address(address: str) -> str:
    if address.startswith("tcp://"):
        return address
    return f"tcp://{address}"


def _split_host_port(address: str) -> tuple[str, int]:
    addr = address.replace("tcp://", "")
    if ":" not in addr:
        return addr, 22268
    host, port = addr.rsplit(":", 1)
    return host, int(port)


def _is_port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect((host, port))
        s.shutdown(2)
        return True
    except Exception:
        return False
    finally:
        s.close()


def ensure_ocr_server_started() -> bool:
    from module.server.setting import State

    deploy_config = State.deploy_config
    if not deploy_config.StartOcrServer:
        return False

    if deploy_config.OcrServerPort:
        port = int(deploy_config.OcrServerPort)
    else:
        _, port = _split_host_port(str(deploy_config.OcrClientAddress))
    host = "0.0.0.0"

    if _is_port_in_use("127.0.0.1", port):
        logger.info(f"OCR server already running on port {port}")
        return True

    global _OCR_SERVER_PROCESS
    if _OCR_SERVER_PROCESS is not None and _OCR_SERVER_PROCESS.poll() is None:
        logger.info("OCR server process already started")
        return True

    _OCR_SERVER_PROCESS = _spawn_server_process(host, port)
    if _OCR_SERVER_PROCESS is None:
        return False

    logger.info(f"Start OCR server on {host}:{port}")
    for _ in range(100):
        if _is_port_in_use("127.0.0.1", port):
            return True
        # 进程提前退出说明启动失败，不用再等
        if _OCR_SERVER_PROCESS.poll() is not None:
            logger.error(f"OCR server exited with code {_OCR_SERVER_PROCESS.returncode}")
            _OCR_SERVER_PROCESS = None
            return False
        time.sleep(0.1)
    logger.error(f"OCR server is not ready on port {port}")
    return False


def _spawn_server_process(host: str, port: int) -> Optional[subprocess.Popen]:
    """以独立进程启动 OCR 服务。

    走 `python -m module.ocr.server_boot` 而不是 multiprocessing：
    Windows spawn 会 re-import 本模块（顶层已 import zerorpc/gevent），
    导致服务进程里 onnxruntime 的 DLL 初始化失败。
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    command = [sys.executable, '-m', 'module.ocr.server_boot',
               '--host', str(host), '--port', str(port)]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
    try:
        return subprocess.Popen(
            command,
            cwd=project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as e:
        logger.error(f'Failed to spawn OCR server process: {e}')
        return None


def shutdown_ocr_server(timeout: float = 2.0) -> bool:
    global _OCR_SERVER_PROCESS

    process = _OCR_SERVER_PROCESS
    if process is None:
        return False

    if process.poll() is not None:
        _OCR_SERVER_PROCESS = None
        return False

    logger.info("Stopping OCR server process")
    try:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("OCR server process did not exit in time, force killing")
            process.kill()
            process.wait(timeout=1.0)
        logger.info("OCR server process stopped")
        return True
    except Exception as e:
        logger.exception(e)
        return False
    finally:
        _OCR_SERVER_PROCESS = None


def notify_ocr_instance_state(instance_id: str, active: bool) -> bool:
    """通知 OCR RPC 某个实例是否正在执行任务。"""
    try:
        from module.server.setting import State
        deploy_config = State.deploy_config
    except Exception as e:
        logger.debug(f'OCR instance state configuration unavailable: {e}')
        return False
    try:
        use_server = bool(deploy_config.UseOcrServer)
        start_server = bool(deploy_config.StartOcrServer)
    except Exception as e:
        logger.debug(f'OCR instance state options unavailable: {e}')
        return False
    if not use_server or not start_server:
        return False

    address = deploy_config.OcrClientAddress or '127.0.0.1:22268'
    client = _get_ocr_control_client(address)
    if client is None:
        return False

    try:
        return bool(client.set_instance_active(str(instance_id), bool(active)))
    except Exception as e:
        _reset_ocr_control_client(client)
        logger.debug(f'OCR instance state notification failed: {e}')
        # RPC 服务可能刚完成重启，当前通知重连一次，避免丢失本次任务边界。
        client = _get_ocr_control_client(address)
        if client is None:
            return False
        try:
            return bool(client.set_instance_active(str(instance_id), bool(active)))
        except Exception as retry_error:
            _reset_ocr_control_client(client)
            logger.debug(f'OCR instance state retry failed: {retry_error}')
            return False


def _get_ocr_control_client(address: str):
    """复用每个实例进程的轻量控制连接，避免每个任务重复握手。"""
    global _OCR_CONTROL_CLIENT, _OCR_CONTROL_ADDRESS
    normalized = _normalize_address(address)
    with _OCR_CONTROL_LOCK:
        if _OCR_CONTROL_CLIENT is not None and _OCR_CONTROL_ADDRESS == normalized:
            return _OCR_CONTROL_CLIENT
        try:
            host, port = _split_host_port(address)
            # 服务未监听时快速返回，避免每个无 OCR 任务边界等待 RPC 超时。
            if not _is_port_in_use(host, port, timeout=0.1):
                return None
            client = zerorpc.Client(timeout=0.5)
            client.connect(normalized)
            client.ping()
        except Exception as e:
            logger.debug(f'OCR RPC control connection failed: {e}')
            return None
        _OCR_CONTROL_CLIENT = client
        _OCR_CONTROL_ADDRESS = normalized
        return client


def _reset_ocr_control_client(client) -> None:
    """连接失效后清空缓存，下一次状态通知会重新连接。"""
    global _OCR_CONTROL_CLIENT, _OCR_CONTROL_ADDRESS
    with _OCR_CONTROL_LOCK:
        if _OCR_CONTROL_CLIENT is client:
            _OCR_CONTROL_CLIENT = None
            _OCR_CONTROL_ADDRESS = None


def serve_forever(host: str, port: int) -> None:
    """在当前进程里启动 zerorpc 服务并阻塞。

    调用方必须已经先 import 过 onnxruntime（见 module/ocr/server_boot.py）：
    gevent 打补丁后再加载 ORT 的原生 DLL 会失败。
    """
    server = zerorpc.Server(OcrServer())
    server.bind(f"tcp://{host}:{port}")
    server.run()


def _get_server_model():
    """RPC 服务端使用的模型。

    与本地推理走同一个工厂，保证两条路径识别行为一致。
    独立成函数便于测试注入，也让 rapidocr 的加载留在服务进程内。
    """
    from module.ocr.models import get_local_ocr_model
    return get_local_ocr_model('ch')


class OcrServer:
    """常驻 RPC 服务端，按需加载并回收 OCR 模型。"""

    # 模型连续 10 分钟未被调用时释放，RPC 监听进程本身继续保留。
    MODEL_IDLE_TIMEOUT = 10 * 60
    MODEL_IDLE_CHECK_INTERVAL = 30

    def __init__(
        self,
        idle_timeout: float = MODEL_IDLE_TIMEOUT,
        idle_check_interval: float = MODEL_IDLE_CHECK_INTERVAL,
    ) -> None:
        # 服务启动时不构造模型，避免空闲实例也占用 GPU。
        self.model = None
        self._model_lock = threading.Lock()
        self._active_requests = 0
        self._active_instances: set[str] = set()
        self._instance_tracking_enabled = False
        self._last_used = time.monotonic()
        self._idle_timeout = max(0.0, float(idle_timeout))
        self._idle_check_interval = max(0.01, float(idle_check_interval))
        self._idle_monitor = threading.Thread(
            target=self._release_idle_model,
            name='ocr-model-idle-monitor',
            daemon=True,
        )
        self._idle_monitor.start()

    def ping(self) -> bool:
        return True

    def set_instance_active(self, instance_id: str, active: bool) -> bool:
        """记录实例任务状态，全部实例空闲后立即释放 OCR 模型。"""
        instance_id = str(instance_id or '').strip()
        if not instance_id:
            return False
        with self._model_lock:
            if active:
                self._instance_tracking_enabled = True
                self._active_instances.add(instance_id)
                return True
            if not self._instance_tracking_enabled:
                return True
            self._active_instances.discard(instance_id)
            if not self._active_instances and not self._active_requests:
                self._release_model_locked('all instances are idle')
            return True

    def _acquire_model(self):
        """获取模型并记录进行中的请求，模型只在首次 OCR 时加载。"""
        with self._model_lock:
            if self.model is None:
                self.model = _get_server_model()
                logger.info('OCR model loaded on first request')
            self._active_requests += 1
            self._last_used = time.monotonic()
            return self.model

    def _release_request(self) -> None:
        with self._model_lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_used = time.monotonic()
            if (self._instance_tracking_enabled and not self._active_instances
                    and not self._active_requests):
                self._release_model_locked('all instances are idle')

    def _run_with_model(self, callback):
        model = self._acquire_model()
        try:
            return callback(model)
        finally:
            self._release_request()

    def _release_idle_model(self) -> None:
        """回收空闲模型，避免后台线程在识别进行时清理模型。"""
        while True:
            time.sleep(self._idle_check_interval)
            with self._model_lock:
                if self.model is None:
                    continue
                idle_seconds = time.monotonic() - self._last_used
                if self._active_requests or idle_seconds < self._idle_timeout:
                    continue
                self._release_model_locked('10 minutes without requests')

    def _release_model_locked(self, reason: str) -> bool:
        """在持有模型锁时清理模型及工厂缓存。"""
        if self.model is None:
            return False
        self.model = None
        # 清理统一工厂缓存，随后由 gc 尽快释放推理引擎及其 GPU 资源。
        from module.ocr.models import clear_ocr_model_cache
        clear_ocr_model_cache()
        gc.collect()
        logger.info(f'OCR model released: {reason}')
        return True

    def detect_and_ocr(
        self,
        image_bytes: bytes,
        drop_score: float = 0.5,
        unclip_ratio: Optional[float] = None,
        box_thresh: Optional[float] = None,
        vertical: bool = False,
    ) -> List[Dict[str, Any]]:
        def recognize(model):
            # 竖排旋转已下沉到 RapidOcrModel 内部，服务端只做参数转发与序列化
            image = pickle.loads(image_bytes)
            results = model.detect_and_ocr(image,
                                           drop_score=drop_score,
                                           unclip_ratio=unclip_ratio,
                                           box_thresh=box_thresh,
                                           vertical=vertical)
            return [
                {"box": _box_to_list(r.box), "ocr_text": r.ocr_text, "score": float(r.score)}
                for r in results
            ]

        return self._run_with_model(recognize)

    def ocr_single_line(self, image_bytes: bytes):
        def recognize(model):
            image = pickle.loads(image_bytes)
            result, score = model.ocr_single_line(image)
            return result, float(score)

        return self._run_with_model(recognize)


def _box_to_list(box) -> list:
    """检测框统一转成嵌套 list，msgpack 无法序列化 ndarray。"""
    return box.tolist() if hasattr(box, 'tolist') else box


class ModelProxy:
    """OCR RPC 客户端代理，接口与本地 RapidOcrModel 一致。"""

    is_proxy = True

    # 单次调用超时。首次调用要加载模型（GPU 建 session 更慢），
    # 因此给足余量；正常单帧只需数百毫秒。
    TIMEOUT = 120
    # 连接握手重试次数：服务进程刚拉起时端口已监听但模型可能还在加载
    CONNECT_RETRY = 3

    def __init__(self, address: str, timeout: int = None) -> None:
        self.address = _normalize_address(address)
        self.timeout = self.TIMEOUT if timeout is None else timeout
        # 不设 timeout 时服务异常会让调用永久阻塞，任务线程就此卡死
        self.client = zerorpc.Client(timeout=self.timeout)
        last_error = None
        for attempt in range(self.CONNECT_RETRY):
            try:
                self.client.connect(self.address)
                self.client.ping()
                return
            except Exception as e:
                last_error = e
                logger.warning(f'OCR server ping failed ({attempt + 1}/{self.CONNECT_RETRY}): {e}')
                time.sleep(1.0)
        raise ScriptError(f"OCR server connection failed: {self.address}") from last_error

    def ocr_single_line(self, image: np.ndarray):
        payload = pickle.dumps(image, protocol=4)
        return self.client.ocr_single_line(payload)

    def detect_and_ocr(
        self,
        image: np.ndarray,
        drop_score: float = 0.5,
        unclip_ratio: Optional[float] = None,
        box_thresh: Optional[float] = None,
        vertical: bool = False,
    ):
        payload = pickle.dumps(image, protocol=4)
        results = self.client.detect_and_ocr(payload, drop_score, unclip_ratio, box_thresh, vertical)
        return [
            BoxedResult(item["box"], None, item["ocr_text"], item["score"])
            for item in results
        ]


atexit.register(shutdown_ocr_server)
