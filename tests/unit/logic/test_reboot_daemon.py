# This Python file uses the following encoding: utf-8
"""reboot_daemon 拉起判定的纯逻辑测试。

只测判定逻辑，不构造真实 RebootDaemon（其 __init__ 会读配置、配日志、起 NapCat、
注册定时重启），统一用 object.__new__ 绕过后手工填被测方法用到的属性。
"""
import asyncio
import importlib.util
import logging
import sys
import types
from collections import defaultdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DAEMON_PY = PROJECT_ROOT / 'dev_tools' / 'reboot' / 'reboot_daemon.py'


def _load_daemon_module():
    """按路径加载 reboot_daemon（它不在包目录下，无法常规 import）。"""
    spec = importlib.util.spec_from_file_location('_reboot_daemon_under_test', DAEMON_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = _load_daemon_module()
RebootDaemon = daemon_mod.RebootDaemon
InstanceConfig = daemon_mod.InstanceConfig


def _make_daemon(state='INACTIVE', schedule=None, max_attempts=3,
                 restart_count=0, pending_verify=False, gave_up=False):
    """构造只填了判定所需属性的 daemon 实例。"""
    d = object.__new__(RebootDaemon)
    d.logger = logging.getLogger('test_reboot_daemon')
    d.instance_configs = {
        'oas1': InstanceConfig(name='oas1', enabled=True, auto_restart=True,
                               max_restart_attempts=max_attempts, restart_cooldown=0)
    }
    d.instance_states = {'oas1': state}
    d.instance_restart_counts = defaultdict(int)
    d.instance_restart_counts['oas1'] = restart_count
    d.instance_last_restart = {}
    d.instance_schedules = {'oas1': schedule} if schedule is not None else {}
    d.instance_pending_verify = {'oas1': pending_verify}
    d.instance_gave_up = {'oas1': gave_up}
    return d


# ──────────────────────── 启用任务判据 ────────────────────────

def test_has_enabled_task_true_when_pending_non_empty():
    # pending 非空 = 有任务已过期该跑
    d = _make_daemon(schedule={'running': {}, 'pending': [{'name': 'Restart'}], 'waiting': []})
    assert d._has_enabled_task('oas1') is True


def test_has_enabled_task_true_when_running_non_empty():
    # running 非空表示 server 认为任务正在执行，实例却 INACTIVE 即异常
    d = _make_daemon(schedule={'running': {'name': 'Restart'}, 'pending': [], 'waiting': []})
    assert d._has_enabled_task('oas1') is True


def test_has_enabled_task_true_when_only_waiting():
    """只有 waiting 任务也必须判为「该有进程」。

    回归：实例崩在只剩 waiting 任务的时刻（实测配置损坏退出），旧判据只认
    pending/running，判成正常空闲不拉起，那个 waiting 任务就永远等不到执行——
    没有进程在等它。「实例活着但空闲」那个场景状态是 RUNNING，不走这条判定。
    """
    d = _make_daemon(schedule={
        'running': {}, 'pending': [],
        'waiting': [{'name': 'KekkaiUtilize', 'next_run': '2026-08-18 02:10:56'}],
    })
    assert d._has_enabled_task('oas1') is True


def test_has_enabled_task_false_when_all_queues_empty():
    # 三个队列全空 = 所有任务都禁用了，拉起也没意义
    d = _make_daemon(schedule={'running': {}, 'pending': [], 'waiting': []})
    assert d._has_enabled_task('oas1') is False


def test_has_enabled_task_false_without_snapshot():
    # 拿不到调度快照时不拉起，下一轮会重新收到推送
    d = _make_daemon()
    assert d._has_enabled_task('oas1') is False


def test_has_enabled_task_false_on_malformed_snapshot():
    d = _make_daemon(schedule='not-a-dict')
    assert d._has_enabled_task('oas1') is False


# ──────────────────────── 重启上限与放弃 ────────────────────────

def test_should_auto_restart_allows_below_limit():
    d = _make_daemon(restart_count=2, max_attempts=3)
    assert d._should_auto_restart('oas1') is True


def test_should_auto_restart_gives_up_at_limit():
    # 达上限 → 放弃并置终态标记
    d = _make_daemon(restart_count=3, max_attempts=3)
    assert d._should_auto_restart('oas1') is False
    assert d.instance_gave_up['oas1'] is True


def test_should_auto_restart_stays_given_up():
    # 放弃后即便计数被清零也不再拉起，直到守护进程重启
    d = _make_daemon(restart_count=0, gave_up=True)
    assert d._should_auto_restart('oas1') is False


def test_should_auto_restart_respects_cooldown():
    d = _make_daemon(restart_count=0)
    d.instance_configs['oas1'].restart_cooldown = 600
    d.instance_last_restart['oas1'] = daemon_mod.time.time()
    assert d._should_auto_restart('oas1') is False


def test_should_auto_restart_false_when_auto_restart_off():
    d = _make_daemon()
    d.instance_configs['oas1'].auto_restart = False
    assert d._should_auto_restart('oas1') is False


# ──────────────────────── 拉起成功需稳定 RUNNING ────────────────────────

def test_restart_instance_marks_pending_verify_not_reset():
    """start 指令送达不再直接归零计数，只标记待验证。

    旧逻辑在此处归零，导致「拉起后立刻又死」也算成功，重启上限永远失效。
    """
    d = _make_daemon(restart_count=2)
    d._ws_stop_instance = lambda name: _async_return(True)
    d._ws_start_instance = lambda name: _async_return(True)
    asyncio.run(d._restart_instance('oas1'))
    assert d.instance_pending_verify['oas1'] is True
    # 计数保持不变，等下个周期验证
    assert d.instance_restart_counts['oas1'] == 2


def test_restart_instance_counts_failure_when_start_fails():
    d = _make_daemon(restart_count=1)
    d._ws_stop_instance = lambda name: _async_return(True)
    d._ws_start_instance = lambda name: _async_return(False)
    asyncio.run(d._restart_instance('oas1'))
    assert d.instance_restart_counts['oas1'] == 2


def test_waiting_only_instance_cannot_loop_forever():
    """判据放宽到 waiting 后，防空拉的唯一防线是重启上限 + 放弃标记。

    只有 waiting 任务的实例现在会被判为该拉起。若它真的拉不起来，必须在
    max_restart_attempts 次后放弃，否则每个冷却周期都会空拉一次。
    """
    d = _make_daemon(max_attempts=3, schedule={
        'running': {}, 'pending': [],
        'waiting': [{'name': 'KekkaiUtilize', 'next_run': '2026-08-18 02:10:56'}],
    })
    assert d._has_enabled_task('oas1') is True

    # 连续 3 轮「拉起→仍未稳定」耗尽配额
    for expected in (1, 2, 3):
        assert d._should_auto_restart('oas1') is True
        d.instance_restart_counts['oas1'] = expected

    # 第 4 轮起放弃，且此后即便计数被清零也不再拉起
    assert d._should_auto_restart('oas1') is False
    assert d.instance_gave_up['oas1'] is True
    d.instance_restart_counts['oas1'] = 0
    assert d._should_auto_restart('oas1') is False


async def _async_return(value):
    return value


# ──────────────────────── OAS Server 存活判定 ────────────────────────

class _FakeProc:
    """伪 Popen：poll() 返回 None 表示进程句柄还在。"""

    def __init__(self, pid=11548, alive=True):
        self.pid = pid
        self._alive = alive
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


def _server_daemon(online_sequence, proc=None):
    """构造只填了 server 管理所需属性的 daemon。

    online_sequence: _is_server_online 的依次返回值，用尽后沿用最后一个。
    """
    d = object.__new__(RebootDaemon)
    d.logger = logging.getLogger('test_reboot_daemon')
    d.oas_process = proc
    d.api_host = '127.0.0.1'
    d.api_port = 22288
    d.api_url = 'http://127.0.0.1:22288'
    d.server_restart_on_crash = True
    d.server_startup_timeout = 1
    seq = list(online_sequence)
    d._online_calls = []

    def is_online():
        value = seq.pop(0) if len(seq) > 1 else seq[0]
        d._online_calls.append(value)
        return value

    d._is_server_online = is_online
    return d


def test_start_server_reuses_running_service_without_spawning(monkeypatch):
    """服务在线就直接复用，不再启动第二个 server。

    这既避免抢占端口，也覆盖「外部手动起了一个 server 占着端口」的情形——
    实测目标机上就出现过守护的 server 与手动起的 server 并存。
    """
    d = _server_daemon([True])
    monkeypatch.setattr(daemon_mod.subprocess, 'Popen',
                        lambda *a, **kw: pytest.fail('服务在线时不应启动新 server'))
    assert d._start_oas_server() is True


def test_start_server_recycles_dead_shell_before_spawning(monkeypatch):
    """句柄存在但服务不可用（空壳）→ 必须先回收残留进程，再启动新的。

    回归 server_restart_on_crash 永久失效：Web UI 点「关闭服务器」或 uvicorn 自行
    退出后，Windows 下 pythonw 外壳会残留——端口已释放、poll() 仍返回 None。旧代码
    只看 poll() 就返回 True，于是守护「检测到离线、决定重启」却什么都没做，此后
    再不记录一行 server 日志（实测沉默 14 小时，期间自动重启能力完全丧失）。
    """
    shell = _FakeProc(pid=11548, alive=True)
    # 第一次判定离线（触发回收），启动后判定在线
    d = _server_daemon([False, True], proc=shell)
    spawned = []

    class _NewProc(_FakeProc):
        pass

    def fake_popen(cmd, **kw):
        spawned.append(cmd)
        return _NewProc(pid=22222)

    monkeypatch.setattr(daemon_mod.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(daemon_mod.time, 'sleep', lambda s: None)
    d._get_server_port = lambda: 22288

    assert d._start_oas_server() is True
    # 空壳被回收，而不是被当成「已在运行」放过
    assert shell.terminated is True
    # 确实启动了新 server
    assert len(spawned) == 1


def test_terminate_server_process_clears_handle():
    """回收后必须把句柄置 None，否则已退出的 Popen 会继续骗过后续判断。"""
    proc = _FakeProc(pid=11548, alive=True)
    d = _server_daemon([False], proc=proc)
    d._terminate_server_process()
    assert proc.terminated is True
    assert d.oas_process is None


def test_ensure_server_running_reverifies_after_start(monkeypatch):
    """启动流程报成功也要复验在线，否则守护会以为 server 好着而彻底沉默。"""
    d = _server_daemon([False])
    # 启动路径谎报成功，但服务始终不在线
    d._start_oas_server = lambda: True
    assert d._ensure_server_running() is False


def test_ensure_server_running_true_when_start_succeeds():
    """启动后确认在线才返回 True。"""
    d = _server_daemon([False, True])
    d._start_oas_server = lambda: True
    assert d._ensure_server_running() is True


def test_stop_server_recycles_own_process_even_if_api_targets_another(monkeypatch):
    """API 关闭与子进程回收是两个对象，必须都做。

    回归：api_url 指向的 server 与 self.oas_process 可能不是同一个进程（目标机上
    api_url→手动起的 server、oas_process→自己的空壳）。旧代码用一次 poll() 决定
    是否 terminate，结果杀掉别人的 server、留下自己的空壳。
    """
    shell = _FakeProc(pid=11548, alive=True)
    d = _server_daemon([True], proc=shell)
    killed_api = []

    def fake_get(url, timeout=None):
        killed_api.append(url)
        return types.SimpleNamespace(status_code=200, text='"success"')

    monkeypatch.setattr(daemon_mod.requests, 'get', fake_get)
    d._stop_oas_server()
    # 两步都执行：API 关闭 + 自己的子进程回收
    assert any('kill_server' in u for u in killed_api)
    assert shell.terminated is True
    assert d.oas_process is None
