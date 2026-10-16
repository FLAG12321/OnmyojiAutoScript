"""MuMu 实例的精确进程身份，供健康检查、恢复和窗口归属检查共用。"""

import ntpath
import psutil


MUMU_PROCESS_NAMES = {'mumuvmmheadless.exe', 'mumunxdevice.exe', 'mumuplayer.exe', 'mumunxmain.exe'}


def mumu_process_matches(instance, name: str, cmdline: list[str]) -> bool:
    """匹配完整参数和值，实例 1 不会命中 11，管理器不代表实例存活。"""
    name = (name or '').lower()
    if name not in MUMU_PROCESS_NAMES or not cmdline:
        return False
    if name == 'mumuvmmheadless.exe':
        option, value = '--comment', getattr(instance, 'name', '')
    else:
        # 窗口/主程序还须属于已绑定安装；共享外置 VMM 使用完整 --comment 身份。
        path = getattr(instance, 'path', '')
        executable = cmdline[0].strip('"')
        if path and ntpath.isabs(path) and ntpath.isabs(executable):
            root = ntpath.normcase(ntpath.dirname(ntpath.dirname(path)))
            executable = ntpath.normcase(ntpath.normpath(executable))
            try:
                if ntpath.commonpath([root, executable]) != root:
                    return False
            except ValueError:
                return False  # 不同磁盘的另一套安装不是当前实例。
        target_id = getattr(instance, 'MuMuPlayer12_id', None)
        option, value = '-v', str(target_id) if target_id is not None else ''
    if not value:
        return False
    # 相同选项重复时只接受所有值一致，避免用前一组参数掩盖另一个实例。
    values = [cmdline[index + 1] for index, token in enumerate(cmdline[:-1]) if token == option]
    return bool(values) and all(candidate == value for candidate in values) and cmdline[-1] != option


def query_mumu_processes(instance) -> tuple[list[psutil.Process], str | None]:
    """返回精确目标及查询错误；只有无目标且无错误，才能确认实例已退出。"""
    if not getattr(instance, 'name', ''):
        return [], 'MuMu instance.name unavailable'
    if getattr(instance, 'MuMuPlayer12_id', None) is None:
        return [], f'MuMu instance index unavailable: {instance.name}'
    targets, errors = [], []
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = proc.info.get('name')
                if not name:
                    errors.append(f'pid={proc.pid} process name unavailable')
                    continue
                if name.lower() not in MUMU_PROCESS_NAMES:
                    continue
                cmdline = proc.info.get('cmdline')
                if cmdline is None:
                    errors.append(f'pid={proc.pid} {name} cmdline unavailable')
                    continue
                if mumu_process_matches(instance, name, cmdline):
                    targets.append(proc)
            except psutil.NoSuchProcess:
                # 枚举后已经退出的进程不再构成存活证据。
                continue
            except Exception as e:
                errors.append(f'pid={proc.pid} process query failed: {e}')
    except Exception as e:
        errors.append(f'process enumeration failed: {e}')
    return targets, '; '.join(errors) or None


def mumu_pid_matches(instance, pid: int) -> tuple[bool | None, str]:
    """重新读取窗口 PID 及 MuMu 父链；权限未知返回 None，绝不只凭 PID 放行。"""
    try:
        proc = psutil.Process(pid)
        child_created = None
        while proc is not None:
            name = proc.name()
            # 无关窗口不需要读取受权限限制的命令行，不能因此阻断目标窗口筛选。
            if (name or '').lower() not in MUMU_PROCESS_NAMES:
                return False, f'pid={pid} unrelated process {name!r}'
            cmdline = proc.cmdline()
            created = proc.create_time()
            if child_created is not None and created > child_created:
                return False, f'pid={pid} parent PID reused'
            if mumu_process_matches(instance, name, cmdline):
                return True, f'pid={pid} instance={instance.name} matched pid={proc.pid} cmdline={cmdline!r}'
            if '--comment' in cmdline or '-v' in cmdline:
                # 窗口进程已经声明其他实例时，不能沿父链推翻自己的身份。
                return False, f'pid={pid} different instance cmdline={cmdline!r}'
            child_created = created
            proc = proc.parent()
        return False, f'pid={pid} no matching MuMu instance'
    except psutil.NoSuchProcess:
        return False, f'pid={pid} process exited'
    except Exception as e:
        return None, f'pid={pid} process query failed: {e}'
