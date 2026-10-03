# This Python file uses the following encoding: utf-8
"""准备界面切换援助式神（好友协战式神）。

从 DailyAltAcc/alliedteam.py 抽出，供需要「友军协战」玩法的任务复用。

设计约束：本 mixin **默认不接管任何战斗准备流程**。使用方必须在自己的
`battle_before()` 里显式判断并调用 `battle_before_switch_help()`，否则流程
完全走使用方原有的通用实现（见 `SwitchHelpShikigami` 的类文档）。

依赖使用方同时具备：
- GeneralBattle（提供 `is_in_prepare` / `is_in_real_battle` /
  `I_PREPARE_HIGHLIGHT` / `I_DISABLE_7DAYS_DIFF_SOUL` / `I_CONFIRM_CLOSE_DIFF_SOUL`）
- GameUi（提供 `screenshot` / `appear` / `click` / `swipe` / `appear_then_click` /
  `ui_goto` / `ui_get_current_page` 与 `I_UI_BACK_RED`）
"""
import random
import re
import cv2
from datetime import datetime
from pathlib import Path
from time import sleep
from types import SimpleNamespace

from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.base.utils import save_image
from module.exception import GamePageUnknownError
from module.logger import logger
from tasks.Component.SwitchHelpShikigami.assets import SwitchHelpShikigamiAssets
from tasks.GameUi.page import page_friends, page_main


# 好友协战页的普通副本次数区域；读取原文，避免数字模式把缺失斜杠补成有效次数。
O_FRIEND_HELP_COUNT = RuleOcr(
    roi=(765, 152, 195, 46), area=(765, 152, 195, 46),
    mode='Full', method='Default', keyword='', name='friend_help_count',
)


# 协战账号的游戏累计目标；独立于本轮脚本已执行的战斗场数。
HELP_TARGET_COUNT = 13


class HelpTargetReached(Exception):
    """准备页已达到协战目标，交给任务撤销预增场次并退出准备。"""


def parse_help_count(text: str) -> int | None:
    """只接受完整的 0~15/15，避免孤立数字及 13/150 被当作有效读数。"""
    normalized = _normalize_help_text(text)
    match = re.search(r'(?<!\d)([0-9]|1[0-5])/15(?!\d)', normalized)
    return int(match.group(1)) if match else None


def _normalize_help_text(text: str) -> str:
    """统一 OCR 常见误识字符，供首轮筛选和二次复核共用。"""
    normalized = str(text or '').translate(str.maketrans({
        'I': '1', 'l': '1', 'S': '5', 's': '5',
        'O': '0', 'o': '0', '／': '/',
    }))
    return re.sub(r'\s+', '', normalized)


def _help_text_has_total_15(text: str) -> bool:
    """判断 OCR 候选是否像次数标签中的 15，而不是任意长数字。"""
    normalized = _normalize_help_text(text)
    if not normalized or len(normalized) > 6 or not re.fullmatch(r'[0-9/]+', normalized):
        return False
    # 允许 OCR 丢掉斜杠或前面的次数，后续会在候选左侧重新核对。
    return bool(
        normalized in ('15', '/15')
        or re.fullmatch(r'0{1,3}15', normalized)
        or re.fullmatch(r'(?:[0-9]|1[0-5])/15', normalized)
    )


def _result_xywh(result, roi) -> list[int] | None:
    """把 OCR 四边形坐标转换为屏幕坐标，兼容 numpy 与普通列表。"""
    try:
        points = [(float(point[0]), float(point[1])) for point in result.box]
        x0 = min(point[0] for point in points)
        y0 = min(point[1] for point in points)
        x1 = max(point[0] for point in points)
        y1 = max(point[1] for point in points)
        return [
            int(round(x0 + roi[0])), int(round(y0 + roi[1])),
            int(round(x1 - x0)), int(round(y1 - y0)),
        ]
    except (TypeError, IndexError, ValueError):
        return None


def _result_total_15_xywh(result, roi) -> list[int] | None:
    """从 OCR 文字框右侧提取 15 的相对坐标，避免依赖固定屏幕位置。"""
    box = _result_xywh(result, roi)
    if box is None:
        return None
    normalized = _normalize_help_text(result.ocr_text)
    suffix_start = normalized.rfind('15')
    if suffix_start < 0 or not normalized[suffix_start:].startswith('15'):
        return box
    # 按文字长度估算 15 在检测框中的起点；检测框通常覆盖整段 N/15。
    if box[2] > 100:
        # 检测器把整行或裁剪边界并入文字框时，15 通常位于该框右端。
        return [box[0] + box[2] - 40, box[1], 40, box[3]]
    offset = int(round(box[2] * suffix_start / len(normalized)))
    suffix_width = min(40, max(1, box[2] - offset))
    return [box[0] + offset, box[1], suffix_width, box[3]]


def _result_span(result, text: str, start: int, end: int, total: int):
    """按合并 OCR 文本的相对字符位置拆出一个候选文字框。"""
    try:
        points = [(float(point[0]), float(point[1])) for point in result.box]
        x0 = min(point[0] for point in points)
        y0 = min(point[1] for point in points)
        x1 = max(point[0] for point in points)
        y1 = max(point[1] for point in points)
        span_x0 = x0 + (x1 - x0) * start / total
        span_x1 = x0 + (x1 - x0) * end / total
        return SimpleNamespace(
            ocr_text=text,
            box=[[span_x0, y0], [span_x1, y0], [span_x1, y1], [span_x0, y1]],
        )
    except (TypeError, IndexError, ValueError, ZeroDivisionError):
        return None


def _iter_help_candidates(result):
    """拆分首轮 OCR 合并的多个标签，确保每个 /15 都会单独复核。"""
    normalized = _normalize_help_text(result.ocr_text)
    initial_count = parse_help_count(normalized)
    if initial_count is not None or _help_text_has_total_15(normalized):
        yield result, initial_count
        return

    # OCR 可能把 15/30、15/30、2/15 合并成一条长文本，只提取总数为 15 的片段。
    for match in re.finditer(r'(?:[0-9]|1[0-5])/15', normalized):
        candidate = _result_span(
            result, match.group(0), match.start(), match.end(), len(normalized))
        if candidate is not None:
            # 合并文本只用于给出 15 的相对位置，次数必须由二次窄 OCR 确认。
            yield candidate, None


def _probe_help_count(image, anchor, anchor_roi):
    """在 15 左侧的小区域复核斜杠与前置次数，返回次数和屏幕 ROI。"""
    anchor_xywh = _result_total_15_xywh(anchor, anchor_roi)
    if anchor_xywh is None:
        return None, None
    raw_anchor = _result_xywh(anchor, anchor_roi)
    if raw_anchor is None:
        return None, None
    # 合并框的 15 位置可能落在左、中、右任一侧，按相对坐标逐点尝试，仍不扩大首轮 ROI。
    anchor_boxes = [anchor_xywh]
    if raw_anchor[2] > 100:
        for candidate_x in (
            raw_anchor[0],
            raw_anchor[0] + max(0, raw_anchor[2] // 2 - 20),
            raw_anchor[0] + raw_anchor[2] - 40,
        ):
            candidate = [candidate_x, raw_anchor[1], 40, raw_anchor[3]]
            if candidate not in anchor_boxes:
                anchor_boxes.append(candidate)

    for x, y, width, height in anchor_boxes:
        # 候选框可能只覆盖 15，也可能覆盖整段文本；固定上限避免带入相邻卡片属性。
        probe_width = min(104, max(76, width + 64))
        # 文字框可能从资源 ROI 上沿开始，保留约 35px 高度覆盖完整数字笔画。
        probe_height = min(36, max(30, height))
        center_y = y + max(0, (height - probe_height) // 2)
        for y_offset in (0, -4, 4):
            probe = (max(0, x - 44), max(0, center_y + y_offset),
                     probe_width, probe_height)
            probe_rule = RuleOcr(
                roi=probe, area=probe, mode='Full', method='Default',
                keyword='', name='find_shikigami_help_probe',
            )
            try:
                results = probe_rule.detect_and_ocr(image)
            except Exception:
                logger.exception('协战次数候选二次 OCR 失败，按未识别处理')
                continue

            # 只接受复核窗口内的单个完整结果，拒绝合并相邻卡片的宽文本。
            for result in results:
                count = parse_help_count(result.ocr_text)
                result_roi = _result_xywh(result, probe)
                if count is None or result_roi is None:
                    continue
                if result_roi[2] <= probe_width:
                    return count, result_roi
                # OCR 偶尔仍返回超出局部窗口的框，但文字本身完整时取其 15 锚点。
                return count, _result_total_15_xywh(result, probe)
    return None, None


def _map_scaled_result(result, scale: int):
    """把放大 ROI OCR 的坐标缩回原始 ROI，保持后续坐标换算一致。"""
    try:
        box = [[float(point[0]) / scale, float(point[1]) / scale]
               for point in result.box]
        return SimpleNamespace(ocr_text=result.ocr_text, box=box)
    except (TypeError, IndexError, ValueError, ZeroDivisionError):
        return None


def _detect_help_results(ocr_obj, image):
    """先用资源原始 ROI OCR，失败时只对同一 ROI 做放大增强，不扩大扫描范围。"""
    results = ocr_obj.detect_and_ocr(image)
    if any(list(_iter_help_candidates(result)) for result in results):
        return results

    x, y, width, height = [int(value) for value in ocr_obj.roi]
    crop = image[y:y + height, x:x + width]
    if crop.size == 0:
        return results
    scale = 4
    enlarged = cv2.resize(
        crop, (width * scale, height * scale), interpolation=cv2.INTER_NEAREST)
    # 白色次数文字在原图中对比度低，阈值化只作用于资源 ROI，不改变扫描区域。
    gray = cv2.cvtColor(enlarged, cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    enhanced = cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)
    retry_rule = RuleOcr(
        roi=(0, 0, width * scale, height * scale),
        area=(0, 0, width * scale, height * scale),
        mode='Full', method='Default', keyword='',
        name='find_shikigami_help_enhanced',
    )
    retry_results = retry_rule.detect_and_ocr(enhanced)
    mapped = [_map_scaled_result(result, scale) for result in retry_results]
    return [result for result in mapped if result is not None]


def locate_help_shikigami_anchor(ocr_obj, image) -> tuple[list[int], int | None]:
    """先定位 15，再在其左侧核对完整 N/15 标签。"""
    try:
        results = _detect_help_results(ocr_obj, image)
    except Exception:
        logger.exception('援助式神锚点 OCR 失败，按未识别处理')
        return [0, 0, 0, 0], None

    # 完整结果也必须复核，避免首轮把 0/15 误读成 10/15 后直接返回。
    candidates = []
    for result in results:
        candidates.extend(_iter_help_candidates(result))

    for candidate, initial_count in candidates:
        probed_count, probed_roi = _probe_help_count(image, candidate, ocr_obj.roi)
        if probed_count is not None and probed_roi is not None:
            # 二次结果通常应与首轮一致；仅允许已知的 10/15→0/15 误读被纠正。
            if (initial_count is None or probed_count == initial_count
                    or (initial_count == 10 and probed_count == 0)):
                return probed_roi, probed_count
        if initial_count is not None:
            roi = _result_xywh(candidate, ocr_obj.roi)
            if roi is not None:
                # 首轮框过宽时只保留其 15 侧锚点，避免把整行卡片带入后续匹配。
                if roi[2] > 100:
                    # 宽框未能局部复核时不返回不可靠坐标，交给上层重试。
                    continue
                return roi, initial_count
    return [0, 0, 0, 0], None


def sync_help_battle_target(task, count: int) -> None:
    """首次准备读数决定还需打几场，不把游戏已有次数灌进脚本统计或进度。"""
    if not getattr(task, '_help_target_enabled', False) or getattr(task, '_help_target_synced', False):
        return
    remaining = max(0, HELP_TARGET_COUNT - count)
    # 通用战斗在进入准备前已 +1，本场尚未完成，因此计算上限时要减掉该预增。
    task._help_battle_limit = task.current_count - 1 + remaining
    task._help_target_synced = True
    logger.info(f'准备页协战次数 {count}/15，距离目标 13 次还需 {remaining} 场')
    if remaining == 0:
        raise HelpTargetReached()


def capture_friend_help_image(task):
    """截图前确认普通副本 x/15 已加载；含首次共尝试三次，第三次失败仍留图。"""
    # 每次检查重新置空，三次均失败时不能沿用上一次的有效读数。
    task._friend_help_count = None
    for attempt in range(1, 4):
        task.screenshot()
        # 导航超时也会返回 False；未到好友页时交给外层恢复，不能当成已进入后识别失败。
        if not task.ui_goto(page_friends):
            raise GamePageUnknownError('协战截图前无法进入好友页')
        # 标题可能被临时提示遮住，限时等待后仍取帧检查次数，避免一直停在页签循环。
        page_timer = Timer(10).start()
        while not page_timer.reached():
            task.screenshot()
            # 共用组件素材，师徒探索等未继承协战 assets 的任务也可直接复用。
            if task.appear(SwitchHelpShikigamiAssets.I_FRIEND_HELP_FLAG, interval=1):
                break
            task.appear_then_click(SwitchHelpShikigamiAssets.I_FRIEND_HELP,
                                   action=SwitchHelpShikigamiAssets.C_FRIEND_HELP_CLICK, interval=1)

        # 标题出现不代表次数已刷新；等待后识别，并把同一帧交给保存方。
        sleep(1.5)
        image = task.screenshot()
        try:
            text = O_FRIEND_HELP_COUNT.detect_text(image)
        except Exception:
            # 仅 OCR 异常视为未加载；导航、截图的设备异常继续交给外层恢复。
            logger.exception(f'好友协战次数 OCR 失败（第 {attempt}/3 次），按未识别处理')
            text = ''
        # 必须有完整的 x/15；孤立的 15、13/150 等不算加载成功，0/15 则是有效读数。
        count = parse_help_count(text)
        if count is not None:
            task._friend_help_count = count
            logger.info(f'好友协战次数已加载（第 {attempt}/3 次）: {count}/15')
            return image
        if attempt == 3:
            logger.warning('好友协战次数连续 3 次未识别到 x/15，直接保存第三次截图')
            return image
        logger.warning(f'好友协战次数未识别到 x/15（第 {attempt}/3 次），返回庭院后重试')
        # 只有确实回到庭院才能开始下一次，避免停在同一页却消耗重试次数。
        if not task.ui_goto(page_main):
            raise GamePageUnknownError('协战截图重试前无法返回庭院')


def capture_completed_help_image(task, refill):
    """协战模式按游戏次数补缺额再取最终帧；无增量或补打受阻时留图并标记未完成。"""
    previous_count = None
    blocked = False
    task._help_target_complete = None
    while True:
        image = capture_friend_help_image(task)
        count = task._friend_help_count
        if count is None:
            # 保留三次未加载也截图的约定；已知不足后失去读数不能反而算完成。
            task._help_target_complete = False if previous_count is not None else None
            return image
        if count >= HELP_TARGET_COUNT:
            task._help_target_complete = True
            return image
        if blocked or (previous_count is not None and count <= previous_count):
            # 有效读数严格递增才能继续补打，最多增长到 13，避免无效战斗无限耗体力。
            logger.warning(f'协战补打受阻或次数未增加，当前 {count}/15，保存现场并标记未完成')
            task._help_target_complete = False
            return image
        remaining = HELP_TARGET_COUNT - count
        logger.info(f'截图核对协战次数 {count}/15，返回补打 {remaining} 场后重新核对')
        if not task.ui_goto(page_main):
            raise GamePageUnknownError('协战补打前无法返回庭院')
        previous_count = count
        # 回调只负责一轮补打并退出副本，不递归保存截图；失败后仍重新取帧存证。
        blocked = refill(remaining) is False


def _sanitize_filename_part(name: str) -> str:
    """替换 Windows 文件名非法字符，避免保存失败。"""
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def build_help_screenshot_name(ctx: dict | None, config_name: str, name_mode: str = 'char') -> str:
    """拼协战次数截图的文件名（不含扩展名）。

    :param ctx: 账号上下文，取多账号运行注入的 _stat_ctx，键为 acc/char/svr/sys
    :param config_name: 配置实例名，单账号直跑时作为退化值
    :param name_mode: 'char' 只用角色名（既有行为）；'full' 追加区服/账号名/系统，
                      便于下游按文件名直接关联账号表

    区服、账号或平台缺失时退化为角色名，避免生成下游无法关联的半截文件名。
    下游按角色表生成完整候选名匹配，身份字段自身含下划线也可关联。
    """
    ctx = ctx or {}
    char_name = ctx.get('char') or config_name
    if name_mode == 'full':
        parts = [char_name, ctx.get('svr'), ctx.get('acc'), ctx.get('sys')]
        if all(parts[1:]):
            return '_'.join(_sanitize_filename_part(str(part)) for part in parts)
    return _sanitize_filename_part(str(char_name))


class SwitchHelpShikigami(SwitchHelpShikigamiAssets):
    # 是否启用「准备界面切换援助式神」流程。默认 False：使用方不显式开启时，
    # 本 mixin 的任何方法都不该被调用，战斗准备一切照旧。
    _need_switch_help_shikigami: bool = False
    # 援助式神是否仍需检测上场：确认上场后置 False，后续场次直接点准备。
    # 未确认前保持 True，下一场继续尝试——一次切换失败不再葬送全天场次
    _help_shikigami_detect: bool = True
    # 同一场战斗内援助式神锚点（N/15 标签）连续识别失败次数，每场战斗重置
    _help_anchor_miss: int = 0
    # 锚点识别失败的重试上限：一轮约 1.3s（OCR 0.3s + sleep 1s），3 次约 4s；
    # 超限后本场放弃切换直接开打（保持原有降级语义，不会卡死在准备界面）
    HELP_ANCHOR_RETRY_LIMIT: int = 3

    def enable_help_shikigami_switch(self) -> None:
        """显式开启「准备界面切换援助式神」流程。

        由使用方在合适的时机调用（例如识别出「本账号本阶段需要协战」后），
        调用后 `_need_switch_help_shikigami` 为真，使用方的 `battle_before()`
        才会分发给 `battle_before_switch_help()`。
        """
        self._need_switch_help_shikigami = True
        # 每轮重新开启都回到「需要检测上场」的初始态，避免沿用上一轮的已确认标记
        self._help_shikigami_detect = True

    def locate_help_shikigami(self) -> list:
        """OCR 定位援助式神卡上的协战次数标签（N/15），返回其屏幕 roi。

        原 O_FIND_SHIKIGAMI_HELP（keyword="15"）在准备界面上并不存在 "15" 整串
        文本，实际一直靠 FULL 模式的「keyword 任一单字命中」降级匹配到式神卡旁的
        "N/15" 标签——位置碰巧正确所以平时能用。但候选列表里没有该标签时
        （09-01 角色A、09-06 角色B 两次事故），单字降级会误中 "9999991" 等垃圾串，
        锚点偏移 500+px，从错误位置滑动导致援助式神未上场、全天场次好友协战 0 计数。
        这里先在 find_shikigami_help 自身 ROI 内遍历包含 15 的 OCR 候选，
        再以每个候选的 15 坐标逐个缩小区域复核完整 N/15；匹配不到返回
        [0,0,0,0] 交给调用方按锚点丢失处理，不再使用固定备用扫描区。
        """
        roi, count = locate_help_shikigami_anchor(
            self.O_FIND_SHIKIGAMI_HELP, self.device.image)
        if count is not None:
            # 只在启用 13 次协战目标的任务中调整本轮剩余场数。
            sync_help_battle_target(self, count)
        return roi

    def battle_before_switch_help(self, buff, config, timeout: float = 5) -> bool:
        """战斗前设置（协战版）：在准备界面切换援助式神后再点准备。

        注意本方法**整段替换**通用战斗准备流程，不调用基类的
        `switch_preset_team` / `check_and_open_buff`——与同心战斗的既有行为一致。
        使用方若不希望如此，应自行在分发处补齐。

        :return: True:进入战斗或点击了准备按钮且识别不到准备按钮了
                 False:超过 timeout 还没进入战斗且没点过准备
        """
        timeout_timer = Timer(timeout).start()
        # 每场战斗重置锚点丢失计数：重试上限只约束本场，超限降级后下一场从头计
        self._help_anchor_miss = 0
        while not timeout_timer.reached():
            self.screenshot()
            if self.is_in_real_battle(False):  # 已进入战斗阶段
                return True
            if self.appear_then_click(self.I_DISABLE_7DAYS_DIFF_SOUL, interval=0.6):  # 关闭御魂不一致提示
                continue
            if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.6):  # 确认关闭御魂不一致提示
                continue
            if self.is_in_prepare(False):  # 战斗准备阶段
                timeout_timer.reset()
                if self._help_shikigami_detect:
                    if not self.appear(self.I_FLAG_CHANGE):
                        # 未展开助战列表，先点击切换按钮
                        self.click(self.C_CLICK_CHANGE)
                        sleep(2)
                        continue
                    # 已展开助战列表，OCR 定位助战位并确保援助式神上场
                    self.screenshot()
                    roi = self.locate_help_shikigami()
                    if roi == [0, 0, 0, 0]:
                        # 锚点丢失：先复验上场旗标——滑动后标签可能消失但式神已上场
                        if self.appear(self.I_FLAG_ON_FIELD):
                            self._help_shikigami_detect = False
                        else:
                            # 告警 + 场内重试；超限后本场放弃切换直接开打（标记
                            # 保持 True，下一场继续尝试，一次失败不葬送全天场次）
                            self._help_anchor_miss += 1
                            logger.warning(f'援助式神锚点(N/15标签)未识别到，'
                                           f'第 {self._help_anchor_miss} 次')
                            if self._help_anchor_miss < self.HELP_ANCHOR_RETRY_LIMIT:
                                sleep(1)
                                continue
                            logger.warning(f'连续 {self._help_anchor_miss} 次未识别到锚点，'
                                           f'本场放弃切换直接开打（本场可能不计好友协战），下一场将重试')
                            # 不 continue，落到下方点准备，避免在准备界面死循环
                    else:
                        self._help_anchor_miss = 0
                        self.I_FLAG_ON_FIELD.roi_back = (
                            roi[0] + roi[2] - 81, roi[1] + roi[3] - 160, 130, 160
                        )
                        logger.info(f"I_FLAG_ON_FIELD.roi_back ={self.I_FLAG_ON_FIELD.roi_back}")
                        if not self.appear(self.I_FLAG_ON_FIELD):
                            # 援助式神未上场，滑动将其拖入出战位
                            self.S_SWIPE_SHIKIGAMI.roi_front = (
                                roi[0], roi[1], roi[2], roi[3]
                            )
                            self.swipe(self.S_SWIPE_SHIKIGAMI, 4)
                            sleep(2)
                            continue
                        # 援助式神确认在场：清除切换标记，后续场次直接点准备
                        self._help_shikigami_detect = False
                # 点击准备
                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                    continue
                continue
            logger.info('Wait for preparation page')
            sleep(random.uniform(0.4, 0.8))
        return False

    def _resolve_character_name(self, name_mode: str = 'char') -> str:
        """截图文件名（不含扩展名）。命名规则见模块级 build_help_screenshot_name。"""
        return build_help_screenshot_name(
            getattr(self, '_stat_ctx', None), self.config.config_name, name_mode)

    def save_friend_help_screenshot(self, name_mode: str = 'char', refill=None) -> None:
        """进入好友页校验协战次数；协战任务可传入补打回调，补齐后再保存最终帧。

        路径与同心战斗共用同一套：`screenshots/Battle_Screenshots_<年_月_日>/`。
        两边截的是同一张「好友协战次数」，所以刻意共用而不是各存一份：同一角色同一
        天后跑的覆盖先跑的，最终只留最新一张，正是想要的语义。

        :param name_mode: 'char'（默认）存为 `<角色名>.png`，与既有行为一致；
                          'full' 存为 `<角色名>_<区服>_<账号名>_<系统>.png`，
                          供下游按文件名直接关联账号表。由复用方经
                          `_help_screenshot_name_mode` 注入，其余调用点不传。

        刻意不包 try/except：本方法里的 `ui_goto` 等会抛设备级异常
        （GamePageUnknownError / GameStuckError 等），按项目约定必须穿透到
        script.py 的恢复逻辑；宽泛吞掉会让卡死的游戏一直卡着。
        """
        # 只有协战任务显式提供回调才进入补打闭环，其余调用仅检查页面是否加载。
        image = (capture_completed_help_image(self, refill) if refill is not None
                 else capture_friend_help_image(self))

        now = datetime.now()
        save_dir = Path(f'screenshots/Battle_Screenshots_{now.year}_{now.month:02d}_{now.day:02d}')
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f'{self._resolve_character_name(name_mode)}.png'
        # 保存刚才通过 OCR 校验的帧，第三次识别失败时也保留当次现场。
        save_image(image, str(save_path))
        logger.info(f'好友协战次数截图已保存: {save_path}')

        # 退出好友页：最多等 5s，点到返回键即提前结束
        run_timer = Timer(5).start()
        while 1:
            self.screenshot()
            if run_timer.reached():
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                break
        self.screenshot()
        if self.ui_get_current_page() != page_main:
            self.ui_goto(page_main)
