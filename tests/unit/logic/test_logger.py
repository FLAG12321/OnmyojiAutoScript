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
    return subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
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
