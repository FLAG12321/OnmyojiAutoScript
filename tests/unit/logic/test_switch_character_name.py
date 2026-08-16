# This Python file uses the following encoding: utf-8
"""角色名匹配单元测试（LoginAccount._is_character_name）。

锁住一个真实缺陷：角色名左侧有圆形等级徽章，PP-OCRv6 识别率比旧引擎高，
会把徽章里的等级数字读进同一个文本框，例如目标 js47瑶光 实际读出
'60js47瑶光'。原实现用严格相等比较，导致切角色永远匹配不上、陷入滑动重试。

纯字符串逻辑，不加载模型、不连设备。
"""
import pytest
from unittest import mock

from tasks.Component.SwitchAccount.login_account import LoginAccount

pytestmark = pytest.mark.unit

match = LoginAccount._is_character_name


# ---------------- 等级粘连必须命中 ----------------

@pytest.mark.parametrize('ocr_text, character_name', [
    ('60js47瑶光', 'js47瑶光'),   # 满级，2 位，无空格
    ('15js47瑶光', 'js47瑶光'),   # 2 位，无空格
    ('1js47瑶光', 'js47瑶光'),    # 1 位，无空格
    ('9js47瑶光', 'js47瑶光'),    # 1 位，无空格
    ('60 js47瑶光', 'js47瑶光'),  # 2 位，数字与名字间被识别出空格
    ('39 js67瑶光', 'js67瑶光'),  # 2 位 + 空格
    ('9 js47瑶光', 'js47瑶光'),   # 1 位 + 空格
])
def test_level_prefix_is_stripped(ocr_text, character_name):
    """等级 1~60 任意位数（含带空格）粘连在前都要能匹配上。"""
    assert match(ocr_text, character_name) is True


def test_level_prefix_with_space_matches_production_samples():
    """生产日志里出现的带空格/无空格真实样例。"""
    assert match('60 js41瑶光', 'js41瑶光') is True
    assert match('36ShyMem16', 'ShyMem16') is True
    assert match('42ShyMem12', 'ShyMem12') is True
    assert match('39 ShyMem17', 'ShyMem17') is True
    assert match('46js55瑶光', 'js55瑶光') is True
    assert match('38m2shy4a', 'm2shy4a') is True


def test_plain_name_still_matches():
    """未粘连等级时（检测框没合并）行为不变。"""
    assert match('js47瑶光', 'js47瑶光') is True


# ---------------- 异体字 ----------------

def test_variant_character_is_normalized():
    """游戏内显示「瑤」，配置里写「瑶」，两者必须等价。"""
    assert match('js22瑤光', 'js22瑶光') is True
    assert match('60js22瑤光', 'js22瑶光') is True


def test_server_variant_is_normalized():
    """服务器名异体字「別/别」必须等价（预设猫川別馆 ≡ OCR 读到的猫川别馆）。"""
    assert match('猫川別馆', '猫川别馆') is True
    assert match('猫川别馆', '猫川別馆') is True
    assert match('猫川別馆', '猫川別馆') is True


# ---------------- 不得误匹配 ----------------

@pytest.mark.parametrize('ocr_text', [
    '60js48瑶光',    # 相邻角色，只差一位数字
    '1js48瑶光',
    '60js4瑶光',     # 目标名的前缀
    '60js470瑶光',   # 目标名加了后缀
    '60 js48瑶光',   # 相邻角色 + 空格
    '60 js470瑶光',  # 目标名加后缀 + 空格
])
def test_other_characters_never_match(ocr_text):
    """剥离后要求完全相等，绝不能把相邻角色误判成目标。

    误匹配会点进错误角色，比匹配失败严重得多。
    """
    assert match(ocr_text, 'js47瑶光') is False


@pytest.mark.parametrize('ocr_text', [
    '猫川別馆',        # 服务器名
    '9小时前',         # 上次登录时间
    '上次登录5小时前',
    '',
    '60',
])
def test_non_character_rows_never_match(ocr_text):
    """同一 ROI 里还有服务器名与登录时间，都不能被当成角色名。"""
    assert match(ocr_text, 'js47瑶光') is False


# ---------------- 角色名本身以数字开头 ----------------

def test_numeric_leading_name_matches_itself():
    """角色名本身以数字开头时，原文必须先命中，不能被误剥。

    这是「最短命中优先」的用意：先试剥 0 位。
    """
    assert match('60js47瑶光', '60js47瑶光') is True


def test_numeric_leading_name_with_level():
    """数字开头的角色名再粘上等级也要能匹配。"""
    assert match('6060js47瑶光', '60js47瑶光') is True


def test_numeric_leading_name_with_level_and_space():
    """数字开头的角色名再粘上等级+空格也要能匹配。"""
    assert match('60 60js47瑶光', '60js47瑶光') is True


def test_level_digits_limit_matches_max_level():
    """等级上限 60 是两位数，因此只允许剥最多 2 位。

    放宽到 3 位会让 '60js470瑶光' 这类误匹配成为可能。
    """
    assert LoginAccount.MAX_LEVEL_DIGITS == 2
    # 3 位数字前缀不属于等级，不得剥离
    assert match('123js47瑶光', 'js47瑶光') is False


# ---------------- switch_svr 服务器兜底 ----------------

def _bare_login_account():
    """用 __new__ 绕过 BaseTask.__init__，避免真实 config/device 依赖。"""
    return LoginAccount.__new__(LoginAccount)


def test_switch_svr_normalizes_variant_in_login_form_keyword():
    """登录表单 keyword 应把「別」归一成「别」，让异体字预设也能命中。"""
    obj = _bare_login_account()
    form = mock.Mock()
    obj.O_SA_LOGIN_FORM_SVR_NAME = form
    obj.ocr_appear = mock.Mock(return_value=True)
    obj.switch_character = mock.Mock(return_value=True)

    assert obj.switch_svr('猫川別馆') is True
    # 表单 keyword 已被归一化，且匹配发生在表单这条路上，不再退回角色列表
    assert form.keyword == '猫川别馆'
    obj.ocr_appear.assert_called_once_with(form)
    obj.switch_character.assert_not_called()


def test_switch_svr_falls_back_to_character_list_when_login_form_misses():
    """登录表单没命中时，退回 switch_character 处理角色列表（传原始 svrName）。"""
    obj = _bare_login_account()
    obj.O_SA_LOGIN_FORM_SVR_NAME = mock.Mock()
    obj.ocr_appear = mock.Mock(return_value=False)
    obj.switch_character = mock.Mock(return_value=True)

    assert obj.switch_svr('猫川别馆') is True
    obj.switch_character.assert_called_once_with('猫川别馆')
