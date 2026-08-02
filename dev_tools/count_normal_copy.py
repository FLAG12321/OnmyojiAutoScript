# This Python file uses the following encoding: utf-8
"""
批量 OCR 识别协战界面截图中「普通副本 x/15」的次数。

截图由 tasks/DailyAltAcc/alliedteam.py 保存到
screenshots/Battle_Screenshots_<年_月_日>/<角色名>.png

用法：
    # 不带参数：识别当天的截图目录
    ./toolkit/python.exe -m dev_tools.count_normal_copy

    # 带日期：识别该日期的截图目录
    ./toolkit/python.exe -m dev_tools.count_normal_copy 2026_07_20

    # 带路径：识别任意文件夹，可用于旧版保存在根目录下的日期文件夹
    ./toolkit/python.exe -m dev_tools.count_normal_copy D:\\some\\folder
"""
import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from module.atom.ocr import RuleOcr
from module.logger import logger

# 项目根目录，本文件位于 dev_tools/ 下，向上一级即根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 支持的图片后缀
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg'}

# 截图保存目录与按日期命名的子目录前缀，与 tasks/DailyAltAcc/alliedteam.py 保持一致
SCREENSHOT_DIR = 'screenshots'
SCREENSHOT_PREFIX = 'Battle_Screenshots_'

# 形如 2026_08_02 的日期参数
DATE_PATTERN = re.compile(r'\d{4}_\d{2}_\d{2}')

# 「每日协战次数」区块中「普通副本13/15」所在的固定 ROI（基于 1280x720 截图）
# DigitCounter 模式的后处理会剔除中文，只保留数字和斜杠，因此 ROI 可以把「普通副本」一起框进来
O_NORMAL_COPY = RuleOcr(
    roi=(765, 152, 195, 46),
    area=(765, 152, 195, 46),
    mode="DigitCounter",
    method="Default",
    keyword="",
    name="normal_copy",
)


def resolve_folder(name: str | None) -> Path:
    """
    解析要识别的文件夹。
    :param name: 命令行传入的日期、文件夹名或路径，为 None 时使用当天日期
    :return: 文件夹的绝对路径
    """
    if name is None:
        name = datetime.now().strftime('%Y_%m_%d')

    # 参数不是 2026_08_02 这种日期时，按普通文件夹名或路径处理
    if not DATE_PATTERN.fullmatch(name):
        folder = Path(name)
        return folder if folder.is_absolute() else PROJECT_ROOT / folder

    # 日期优先解析成新的截图目录，仅当它不存在而根目录下的旧文件夹存在时才回退
    dated = PROJECT_ROOT / SCREENSHOT_DIR / f'{SCREENSHOT_PREFIX}{name}'
    legacy = PROJECT_ROOT / name
    if not dated.is_dir() and legacy.is_dir():
        return legacy
    return dated


def read_image(path: Path) -> np.ndarray | None:
    """
    读取图片。使用 imdecode 而非 imread，避免路径含中文时读取失败。
    :param path: 图片路径
    :return: 图片数组，读取失败返回 None
    """
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None


def main() -> int:
    # Windows 下 stdout 默认是 gbk，在 Git Bash 等 UTF-8 终端里中文会乱码，统一改成 UTF-8
    if sys.platform.startswith('win'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description='识别协战截图中「普通副本 x/15」的次数')
    parser.add_argument('folder', nargs='?', default=None,
                        help='日期（形如 2026_08_02，对应 screenshots/Battle_Screenshots_2026_08_02），'
                             '或任意文件夹名/路径；缺省为当天日期')
    args = parser.parse_args()

    folder = resolve_folder(args.folder)
    if not folder.is_dir():
        print(f'文件夹不存在：{folder}')
        return 1

    images = sorted(p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        print(f'文件夹中没有图片：{folder}')
        return 1

    # 压掉 RuleOcr 内部逐张打印的 logger.attr 日志，只保留本脚本的输出
    logger.setLevel(logging.ERROR)

    print(f'{folder}  共 {len(images)} 张图片')
    for path in images:
        image = read_image(path)
        if image is None:
            print(f'{path.name}  读取失败')
            continue

        current, _, total = O_NORMAL_COPY.ocr(image)
        # total 为 0 说明没有匹配到 x/y 形式的文本，视为识别失败
        if total == 0:
            print(f'{path.name}  未识别')
        else:
            print(f'{path.name}  {current}/{total}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
