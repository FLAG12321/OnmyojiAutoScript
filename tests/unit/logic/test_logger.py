import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_logger_snippet(tmp_path, snippet):
    repo_root = Path(__file__).resolve().parents[3]
    package_dir = tmp_path / "module"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    shutil.copyfile(repo_root / "module" / "logger.py", package_dir / "logger.py")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    # 子进程通过管道输出中文；显式指定 UTF-8，避免 Windows 默认代码页与父进程
    # 的 UTF-8 解码不一致，把正确的 GUI 回调文本变成替换字符。
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        # 显式 utf-8：logger 输出含中文，Windows 默认 GBK 解码会抛
        # UnicodeDecodeError 让 result.stdout 变成 None，断言拿不到真实内容
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_import_logger_does_not_use_python_c_as_log_name(tmp_path):
    result = run_logger_snippet(tmp_path, "import module.logger as m; print(m.logger.log_file)")

    assert result.returncode == 0
    assert "_-c.txt" not in result.stdout


def test_file_logger_preserves_underscores_in_explicit_name(tmp_path):
    result = run_logger_snippet(
        tmp_path,
        "import module.logger as m; m.set_file_logger('get_images'); print(m.logger.log_file)",
    )

    assert result.returncode == 0
    assert "_get_images.txt" in result.stdout


def test_rich_console_write_error_does_not_escape_logger_print(tmp_path):
    snippet = r'''
import module.logger as m

class BrokenConsole:
    def print(self, *objects):
        raise OSError(22, "Invalid argument")

m.console_hdlr.console = BrokenConsole()
m.rule("Device")
print("survived")
'''
    result = run_logger_snippet(tmp_path, snippet)

    assert result.returncode == 0
    assert "survived" in result.stdout


def test_flutter_handler_does_not_fold_long_cjk_message(tmp_path):
    r"""推给前端的日志行不得在后端折行，行首与正文必须在同一行。

    回归锚点，用户报的格式退化就是这个：rich 按「单词」折行
    （rich/_wrap.py 的 re_word = r'\s*\S+\s*'），一段无空格的中文就是一个
    68 列的「单词」，行首装饰占 23 列后只剩 57 列放不下它，divide_line 于是
    在词首插换行 —— 第一行只剩 `WARNING |07:42:27.350|`、正文整段掉到第二行
    且不带行首，破坏了要求的 `WARNING |07:42:27.3| 内容` 格式。

    修复需同时做两件事（缺一不可，实测只做前者仍折成两行）：
      1. FlutterHandler.render 不把消息塞进 LogRender 的 Table（Table 按列宽折，
         不受 Console 的 soft_wrap 影响）；
      2. Console 那次 print 传 soft_wrap=True。
    折行改由前端 normalizeLogLines 按显示列硬折。
    """
    snippet = r'''
import module.logger as m

payloads = []
m.logger.set_func_logger(func=payloads.append)
msg = '这是一条比较长的中文日志内容用来触发后端折行看看行首装饰到底占多少列'
m.logger.warning(msg)
body = ''.join(payloads)
lines = [x for x in body.split('\n') if x]
print('LINES=%d' % len(lines))
print('FIRST=%s' % lines[0])
'''
    result = run_logger_snippet(tmp_path, snippet)

    assert result.returncode == 0, result.stderr
    assert 'LINES=1' in result.stdout, (
        f'超宽中文消息被后端折行了，行首会独占一行。实际输出：\n{result.stdout}')
    first = next(line for line in result.stdout.splitlines()
                 if line.startswith('FIRST='))
    first = first[len('FIRST='):]
    assert first.startswith('WARNING |'), f'行首格式被破坏：{first!r}'
    # 行首之后必须紧跟正文，这正是「行首独占一行」的判据
    assert '| 这是一条' in first, f'正文未与行首同行：{first!r}'


def test_flutter_handler_keeps_traceback_box_fixed_width(tmp_path):
    """traceback 例外：它是 rich 画的定宽框，必须保留 Table + 定宽渲染。

    soft_wrap 会把框线拉直，┌─┐ 边框对不上列，异常现场变得没法读。
    """
    snippet = r'''
import module.logger as m

payloads = []
m.logger.set_func_logger(func=payloads.append)
try:
    raise ValueError('探针异常')
except ValueError:
    m.logger.exception('任务异常退出')
lines = [x for x in ''.join(payloads).split('\n') if x]
print('MAXW=%d' % max(len(x) for x in lines))
print('HASBOX=%s' % any(x.startswith('┌') for x in lines))
'''
    result = run_logger_snippet(tmp_path, snippet)

    assert result.returncode == 0, result.stderr
    assert 'HASBOX=True' in result.stdout, \
        f'traceback 的定宽框丢了：\n{result.stdout}'
    # 框宽必须仍受 GUI_LOG_WIDTH 约束
    maxw = int(next(line for line in result.stdout.splitlines()
                    if line.startswith('MAXW=')).split('=')[1])
    assert maxw <= 80, f'traceback 超出 GUI_LOG_WIDTH：{maxw} 列'
