# This Python file uses the following encoding: utf-8
"""Restart 选角逻辑单元测试（LoginHandler 的双值匹配 + 滑动 + 勾选验证）。

锁住改造前的四个缺陷：
1. 只能比对一个值（keyword 槽位被角色名/区服名两条路径塞不同语义），改后双值任一命中；
2. 完全没有滑动，目标不在首屏就永远找不到，改后向上滑动直到「两轮结果相同即到底」；
3. 命中后盲点两次、无选中态验证，改后用勾选标记的 y 与目标文本 y 同条目判定；
4. 确认循环无前置条件，没找到目标也会点确认而登进错号，改后一定带着确定目标进入。

判据都抽成了静态方法，纯数值/字符串逻辑；涉及实例方法的用例走 object.__new__ 裸实例
挂桩，全程不加载 OCR 模型、不连设备。
"""
import pytest

from tasks.Restart.login import (
    LoginHandler,
    SELECT_CHARACTER_Y_TOLERANCE,
    SELECT_CHARACTER_CLICK_RETRY,
    CHARACTER_LIST_SCROLL_LIMIT,
    _normalize_svr,
)

pytestmark = pytest.mark.unit

match_index = LoginHandler._match_character_index
aligned = LoginHandler._is_select_mark_aligned

# 真机版面实测值（log/screenshots/capture_1787184757282.png，1280×720）：
# 条目 1 角色名 js69瑤光 y≈147 / 区服名 神之晚宴 y≈185
# 条目 2 角色名 js15瑤光 y≈270 / 区服名 猫川別馆 y≈307，勾选标记 y≈290
# 行内间距约 37px，条目间距约 123px
ENTRY_SPACING = 123
ROW_SPACING = 37


# ---------------- 双值匹配：角色名或区服名任一命中 ----------------

def test_match_by_character_name():
    """角色名命中（等级数字粘连也要认出来）。"""
    texts = ['35js69瑶光', '神之晚宴', '60js15瑶光', '猫川别馆']
    assert match_index(texts, 'js15瑶光', '') == 2


def test_match_by_svr_name():
    """只填对区服名时也要命中，命中的是区服名那一行。"""
    texts = ['35js69瑶光', '神之晚宴', '60js15瑶光', '猫川别馆']
    assert match_index(texts, '', '猫川别馆') == 3


def test_match_either_value_hits():
    """角色名与区服名都填、都能匹配上时，取列表里靠前的那一个。"""
    texts = ['35js69瑶光', '神之晚宴', '60js15瑶光', '猫川别馆']
    # 区服名 神之晚宴 在下标 1，角色名 js15瑶光 在下标 2，取靠前的 1
    assert match_index(texts, 'js15瑶光', '神之晚宴') == 1


def test_match_takes_first_when_duplicated():
    """同名角色出现多次时取第一个。"""
    texts = ['60js15瑶光', '猫川别馆', '35js15瑶光', '神之晚宴']
    assert match_index(texts, 'js15瑶光', '') == 0


def test_no_match_returns_negative():
    """都没命中返回 -1，由调用方决定是滑动还是兜底。"""
    texts = ['35js69瑶光', '神之晚宴']
    assert match_index(texts, 'js15瑶光', '猫川别馆') == -1


def test_empty_texts_returns_negative():
    """空 OCR 结果不抛异常。"""
    assert match_index([], 'js15瑶光', '猫川别馆') == -1


def test_empty_keywords_never_match():
    """角色名与区服名都为空时不能命中任何一行，否则会误选默认高亮之外的角色。"""
    texts = ['35js69瑶光', '神之晚宴', '', '猫川别馆']
    assert match_index(texts, '', '') == -1


def test_empty_svr_does_not_match_empty_ocr_text():
    """区服名为空 + OCR 读出空串时不能相等成立（空 == 空 的经典陷阱）。"""
    assert match_index(['', '神之晚宴'], 'js15瑶光', '') == -1


# ---------------- 异体字归一 ----------------

@pytest.mark.parametrize('ocr_text, svr', [
    ('猫川別馆', '猫川别馆'),   # 游戏繁体 → 配置简体
    ('猫川别馆', '猫川別馆'),   # 配置繁体 → 游戏简体
    ('猫川別馆', '猫川別馆'),   # 两边都繁体
])
def test_svr_variant_characters_normalized(ocr_text, svr):
    """区服名的「別/别」异体字要双向归一。"""
    assert match_index([ocr_text], '', svr) == 0


@pytest.mark.parametrize('ocr_text, character', [
    ('js15瑤光', 'js15瑶光'),     # 游戏繁体 瑤 → 配置简体 瑶
    ('60js15瑤光', 'js15瑶光'),   # 繁体 + 等级粘连
    ('js15瑶光', 'js15瑤光'),     # 配置繁体
])
def test_character_variant_characters_normalized(ocr_text, character):
    """角色名的「瑤/瑶」归一由 _is_character_name 内部完成，这里锁住它确实生效。"""
    assert match_index([ocr_text], character, '') == 0


def test_normalize_svr_only_touches_variants():
    """归一函数只替换异体字，不动其他内容。"""
    assert _normalize_svr('猫川別馆') == '猫川别馆'
    assert _normalize_svr('js15瑤光') == 'js15瑶光'
    assert _normalize_svr('神之晚宴') == '神之晚宴'


# ---------------- 勾选态 y 容差 ----------------

@pytest.mark.parametrize('delta', [0, 17, 20, SELECT_CHARACTER_Y_TOLERANCE])
def test_mark_aligned_within_same_entry(delta):
    """同条目内：勾选标记与角色名差约 20px、与区服名差约 17px，都必须判成立。"""
    assert aligned(290, 290 - delta) is True
    assert aligned(290, 290 + delta) is True


@pytest.mark.parametrize('delta', [SELECT_CHARACTER_Y_TOLERANCE + 1, ROW_SPACING + 25, ENTRY_SPACING])
def test_mark_not_aligned_across_entries(delta):
    """跨条目：勾选在上一条目、目标在下一条目时必须判不成立，否则静默登进邻号。"""
    assert aligned(290, 290 - delta) is False
    assert aligned(290, 290 + delta) is False


def test_tolerance_within_entry_spacing_bounds():
    """容差不变式：必须大于行内偏移（20px）且小于半个条目间距（61px）。

    小于 20 会让勾选匹到区服名那一行时判不成立、白点三次；大于 61 会让勾选在相邻条目
    也判定成立——那是静默失效，日志里看不出任何异常，直接登进错号。
    """
    assert SELECT_CHARACTER_Y_TOLERANCE > 20
    assert SELECT_CHARACTER_Y_TOLERANCE < ENTRY_SPACING // 2


# ---------------- OCR box → 设备坐标换算 ----------------

def test_ocr_box_to_roi():
    """box 是相对 OCR ROI 的四点坐标，换算结果必须是设备空间的 [x, y, w, h]。"""
    ocr_roi = (110, 120, 350, 600)
    # 一个宽 90、高 24 的文本框，左上角在 ROI 内 (8, 27)
    box = [(8, 27), (98, 27), (98, 51), (8, 51)]
    assert LoginHandler._ocr_box_to_roi(ocr_roi, box) == [118, 147, 90, 24]


# ---------------- 查找流程：滑动、收敛、兜底 ----------------

class _OcrItem:
    """detect_and_ocr 返回项的最小桩。"""

    def __init__(self, text, top=0, height=24):
        self.ocr_text = text
        self.box = [(8, top), (98, top), (98, top + height), (8, top + height)]


class _OcrStub:
    """RuleOcr 的最小桩：只提供 roi 与 detect_and_ocr。

    RuleOcr 是类属性，直接改会污染其他用例，所以挂到实例上遮蔽。
    """
    roi = (110, 120, 350, 600)

    def __init__(self, fn):
        self.detect_and_ocr = fn


def _handler(screens, character='', svr=''):
    """构造只跑查找流程的 LoginHandler 裸实例。

    :param screens: 每次 OCR 依次返回的屏幕内容，每屏是 _OcrItem 列表；用完后重复最后一屏
                    （模拟已滑到底、再滑也不变）
    """
    t = object.__new__(LoginHandler)
    t.character = character
    t.svr = svr
    t.swipes = []
    t.clicks = []
    t._screens = list(screens)
    t._ocr_calls = []

    t.screenshot = lambda: None
    t.device = type('D', (), {'image': None})()
    t.swipe = lambda rule, **kw: t.swipes.append(rule.name)
    t.click = lambda rule, **kw: t.clicks.append(rule.name)

    def detect_and_ocr(image):
        screen = t._screens.pop(0) if len(t._screens) > 1 else t._screens[0]
        t._ocr_calls.append(screen)
        return screen

    t.O_LOGIN_SPECIFIC_SERVE = _OcrStub(detect_and_ocr)
    return t


def test_one_ocr_per_round_no_swipe_when_hit_on_first_screen():
    """首屏就命中：只 OCR 一次、不滑动。"""
    t = _handler([[_OcrItem('60js15瑶光', top=27), _OcrItem('猫川别馆', top=64)]],
                 character='js15瑶光')
    click, target_y = t._find_login_character()
    assert click is not None
    assert len(t._ocr_calls) == 1, '一轮只能 OCR 一次'
    assert t.swipes == [], '命中就不该滑动'
    # 文本框中心 y = roi[1] + (27 + 51) // 2 = 120 + 39
    assert target_y == 159


def test_swipes_until_target_appears():
    """首屏没有、滑一次后出现：滑动 1 次，OCR 2 次。"""
    screen1 = [_OcrItem('35js69瑶光', top=27), _OcrItem('神之晚宴', top=64)]
    screen2 = [_OcrItem('60js15瑶光', top=27), _OcrItem('猫川别馆', top=64)]
    t = _handler([screen1, screen2], character='js15瑶光')
    click, _ = t._find_login_character()
    assert click is not None
    assert len(t._ocr_calls) == 2
    assert t.swipes == [LoginHandler.S_LOGIN_CHARACTER_LIST_UP.name]


def test_converges_when_two_rounds_identical_and_falls_back_to_first():
    """两轮结果相同即到底，取当前屏（列表底部）第一条兜底，不抛异常。"""
    screen = [_OcrItem('35js69瑶光', top=27), _OcrItem('神之晚宴', top=64)]
    t = _handler([screen], character='不存在的角色', svr='不存在的区服')
    click, target_y = t._find_login_character()
    assert click is not None, '滑到底也要给出兜底目标'
    # 第 1 轮记下结果并滑动，第 2 轮发现相同 → 判定到底
    assert len(t._ocr_calls) == 2
    assert t.swipes == [LoginHandler.S_LOGIN_CHARACTER_LIST_UP.name]
    # 兜底取下标 0，中心 y = 120 + (27 + 51) // 2
    assert target_y == 159


def test_empty_screen_returns_no_target_without_indexerror():
    """空屏保护必须排在收敛判定之前：首轮空屏不能因 [] == 初值 判成到底再取 texts[0] 崩掉。"""
    t = _handler([[]], character='js15瑶光')
    click, target_y = t._find_login_character()
    assert click is None
    assert target_y is None
    assert t.swipes == [], '空屏不该滑动'


def test_no_keyword_skips_lookup_entirely():
    """未指定角色和区服：不 OCR、不滑动，直接返回空目标去登默认角色（保持原行为）。"""
    t = _handler([[_OcrItem('60js15瑶光')]])
    click, target_y = t._find_login_character()
    assert click is None
    assert target_y is None
    assert t._ocr_calls == [], '未指定目标时不该 OCR'
    assert t.swipes == []


def test_scroll_limit_stops_runaway_loop():
    """每轮结果都不同且都不命中时（滑动残影导致 OCR 抖动），滑动轮数必须被上限截断。"""
    screens = [[_OcrItem(f'35js{i}瑶光', top=27)] for i in range(CHARACTER_LIST_SCROLL_LIMIT + 5)]
    t = _handler(screens, character='js999瑶光')
    click, target_y = t._find_login_character()
    assert click is None
    assert target_y is None
    assert len(t._ocr_calls) == CHARACTER_LIST_SCROLL_LIMIT


# ---------------- 选中态验证 ----------------

def _select_handler(mark_ys):
    """构造只跑选中验证的裸实例。

    :param mark_ys: 每轮 appear(I_SELECT_CHARACTER) 时勾选标记的中心 y；None 表示这一轮
                    没匹配到勾选标记
    """
    t = object.__new__(LoginHandler)
    t.clicks = []
    t._rounds = list(mark_ys)
    t._consumed = []
    t.screenshot = lambda: None
    t.click = lambda rule, **kw: t.clicks.append(rule.name)

    class _Mark:
        # RuleImage.match 命中时会把位置回写进 roi_front，这里模拟回写后的值
        roi_front = [327, 0, 63, 40]
        name = 'select_character'

    t.I_SELECT_CHARACTER = _Mark()

    def appear(rule, **kw):
        y = t._rounds.pop(0) if t._rounds else None
        t._consumed.append(y)
        if y is None:
            return False
        # 反推 roi_front[1]，使 mark[1] + mark[3] // 2 == y
        _Mark.roi_front[1] = y - 40 // 2
        return True

    t.appear = appear
    return t


def test_already_selected_needs_zero_click():
    """进来时默认高亮恰好就是目标：先验证再点击，所以零点击直接通过。"""
    t = _select_handler([290])
    assert t._ensure_character_selected(object(), 290) is True
    assert t.clicks == [], '已选中就不该再点'


def test_clicks_then_verifies():
    """首轮勾选在别的条目上，点一次后对齐即通过。"""
    t = _select_handler([290 - 123, 290])
    assert t._ensure_character_selected(
        type('R', (), {'name': 'character select'})(), 290) is True
    assert len(t.clicks) == 1


def test_mark_missing_still_clicks_and_gives_up_after_retries():
    """始终匹配不到勾选标记：点满重试次数后返回 False，但不抛异常。"""
    t = _select_handler([None] * SELECT_CHARACTER_CLICK_RETRY)
    assert t._ensure_character_selected(
        type('R', (), {'name': 'character select'})(), 290) is False
    assert len(t.clicks) == SELECT_CHARACTER_CLICK_RETRY


def test_never_aligned_gives_up_without_exception():
    """勾选一直落在邻条目：验证失败但不中断流程，交给调用方继续确认。"""
    t = _select_handler([290 - 123] * SELECT_CHARACTER_CLICK_RETRY)
    assert t._ensure_character_selected(
        type('R', (), {'name': 'character select'})(), 290) is False
    assert len(t.clicks) == SELECT_CHARACTER_CLICK_RETRY


# ---------------- 资源与接口约定 ----------------

def test_swipe_asset_moves_up_400px():
    """滑动资源必须是纵向向上 400px，x 恒在 OCR ROI 中线附近。"""
    s = LoginHandler.S_LOGIN_CHARACTER_LIST_UP
    front, back = s.roi_front, s.roi_back
    start_y = front[1] + front[3] // 2
    end_y = back[1] + back[3] // 2
    assert end_y - start_y == -400, '必须向上滑 400px'
    assert front[0] == back[0], 'x 起点终点必须一致（纯纵向）'
    # 起点和终点都要落在 OCR ROI（x 110→460, y 120→720）内
    ocr_roi = LoginHandler.O_LOGIN_SPECIFIC_SERVE.roi
    assert ocr_roi[0] <= front[0] and front[0] + front[2] <= ocr_roi[0] + ocr_roi[2]
    assert ocr_roi[1] <= end_y <= ocr_roi[1] + ocr_roi[3]
    assert ocr_roi[1] <= start_y <= ocr_roi[1] + ocr_roi[3]


def test_set_specific_usr_accepts_both_values():
    """切号路径传两个值；只传一个时区服名兜底成角色名。"""
    t = object.__new__(LoginHandler)
    t.set_specific_usr('js15瑶光', '猫川别馆')
    assert (t.character, t.svr) == ('js15瑶光', '猫川别馆')

    t.set_specific_usr('猫川别馆')
    assert (t.character, t.svr) == ('猫川别馆', '猫川别馆')


def test_select_mark_search_area_covers_ocr_roi():
    """勾选标记的搜索范围必须覆盖 OCR ROI 的主体，否则目标滑到边缘时找不到勾选。

    实测：搜索范围 roi_back=(7,8,463,693) → y 8..701，OCR ROI=(110,120,350,600) → y 120..720。
    顶部有余量，底部差 19px；再算上模板本身 41px 高（模板要完整落在搜索范围内），勾选中心
    最大只能到 680，即目标文本中心 y > 710 的条目取不到勾选。那种条目已经贴着屏幕底边、
    只露出半截，正常列表不会停在那个位置；真取不到时代码是点击后重试、超次数记 error 继续
    确认，属于安全降级不会崩。这里锁住底部盲区小于半个条目间距，防止 roi_back 被改小到
    连最后一个完整可见条目的勾选都覆盖不到。
    """
    back = LoginHandler.I_SELECT_CHARACTER.roi_back
    ocr_roi = LoginHandler.O_LOGIN_SPECIFIC_SERVE.roi
    assert back[1] <= ocr_roi[1], '搜索范围顶部必须不低于 OCR ROI 顶部'
    assert back[0] <= ocr_roi[0], '搜索范围左边必须不右于 OCR ROI 左边'
    blind = (ocr_roi[1] + ocr_roi[3]) - (back[1] + back[3])
    assert blind < ENTRY_SPACING // 2, f'底部盲区 {blind}px 不得超过半个条目间距'
