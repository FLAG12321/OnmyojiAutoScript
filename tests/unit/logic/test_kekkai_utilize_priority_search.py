# This Python file uses the following encoding: utf-8
"""结界寄养优先名称搜索的纯逻辑单元测试。"""
from types import SimpleNamespace

import pytest

from module.ocr.result import BoxedResult
from tasks.KekkaiUtilize.config import SelectFriendList, UtilizeRule
from tasks.KekkaiUtilize.script_task import ScriptTask
from tasks.KekkaiUtilize.utils import CardClass
from tasks.Utils.config_enum import ShikigamiClass


def make_task() -> ScriptTask:
    """构造绕过设备初始化的任务实例。"""
    return ScriptTask.__new__(ScriptTask)


@pytest.mark.unit
def test_parse_priority_search_names_keeps_configured_order():
    """区服别名和分隔符解析后应保持用户配置顺序。"""
    raw_names = '同区:瑤光，跨服：别名\nsame_server|角色甲;different_server:角色乙'

    assert ScriptTask.parse_priority_search_names(raw_names) == [
        (SelectFriendList.SAME_SERVER, '瑤光'),
        (SelectFriendList.DIFFERENT_SERVER, '别名'),
        (SelectFriendList.SAME_SERVER, '角色甲'),
        (SelectFriendList.DIFFERENT_SERVER, '角色乙'),
    ]


@pytest.mark.unit
def test_parse_priority_search_names_ignores_unmarked_or_empty_items():
    """未标注区服或名称为空的项目不进入搜索队列。"""
    assert ScriptTask.parse_priority_search_names('瑶光,同区:,跨区:角色乙') == [
        (SelectFriendList.DIFFERENT_SERVER, '角色乙'),
    ]


@pytest.mark.unit
def test_normalize_priority_name_handles_variant_characters_and_spaces():
    """常见异体字和 OCR 空白应归一化后再比较。"""
    assert ScriptTask._normalize_priority_name(' js16 瑤光 別院 ') == 'js16瑶光别院'


@pytest.mark.unit
def test_matching_priority_name_areas_returns_all_matches_in_visual_order():
    """同名结果应按画面纵坐标排序并返回绝对坐标。"""
    task = make_task()
    results = [
        # 第二行检测框故意倾斜，用于验证四点外接矩形换算
        BoxedResult([[20, 120], [80, 118], [82, 145], [19, 147]], None, 'js51瑤光', 0.99),
        BoxedResult([[18, 10], [100, 12], [101, 42], [17, 40]], None, 'js16瑤光', 0.99),
        BoxedResult([[21, 220], [120, 220], [120, 250], [21, 250]], None, '其他角色', 0.99),
    ]
    task.O_NAME_LIST = SimpleNamespace(
        roi=(309, 212, 159, 393),
        detect_and_ocr=lambda image: results,
    )
    task.device = SimpleNamespace(image=object())

    assert task._matching_priority_name_areas('瑶光') == [
        (326, 222, 84, 32),
        (328, 330, 63, 29),
    ]


@pytest.mark.unit
def test_priority_selection_roi_uses_name_y_and_fixed_right_edge():
    """动态选中区域应使用名称纵坐标且固定收口到 x=640。"""
    roi = ScriptTask._priority_selection_roi((325, 252, 108, 33))

    assert roi == (325, 237, 315, 70)
    assert roi[0] + roi[2] == 640


@pytest.mark.unit
def test_card_matches_selected_row_uses_calibrated_center_distance():
    """40像素内应视为当前卡片，相邻好友行必须排除。"""
    selected_area = (609, 420, 22, 39)

    assert ScriptTask._card_matches_selected_row((543, 415, 62, 54), selected_area) is True
    assert ScriptTask._card_matches_selected_row((543, 317, 62, 54), selected_area) is False
    assert ScriptTask._card_matches_selected_row((543, 453, 62, 53), selected_area) is True
    assert ScriptTask._card_matches_selected_row((543, 454, 62, 53), selected_area) is False


@pytest.mark.unit
def test_ensure_card_selected_skips_redundant_click():
    """卡片与选中标记同处一行时不应再次点击。"""
    task = make_task()
    card_area = (543, 415, 62, 54)
    clicks = []
    task.C_SELECT_CARD = SimpleNamespace(roi_front=None)
    task.I_SELECT_REALM_ON = SimpleNamespace(roi_front=(609, 420, 22, 39))
    task.appear = lambda target: True
    task.click = lambda target: clicks.append(target)

    assert task._ensure_card_selected(card_area) is True
    assert task.C_SELECT_CARD.roi_front == card_area
    assert clicks == []


@pytest.mark.unit
def test_ensure_card_selected_waits_for_marker_after_click():
    """目标卡不在当前行时应点击，并在标记移动后立即返回。"""
    task = make_task()
    card_area = (543, 415, 62, 54)
    clicks = []
    appear_count = 0
    task.C_SELECT_CARD = SimpleNamespace(roi_front=None)
    task.I_SELECT_REALM_ON = SimpleNamespace(roi_front=(609, 313, 22, 39))

    def appear(target):
        """第二次识别时模拟选中标记移动到目标行。"""
        nonlocal appear_count
        appear_count += 1
        if appear_count >= 2:
            target.roi_front = (609, 420, 22, 39)
        return True

    task.appear = appear
    task.click = lambda target: clicks.append(target)
    task.screenshot = lambda: None

    assert task._ensure_card_selected(card_area) is True
    assert clicks == [task.C_SELECT_CARD]
    assert appear_count == 2


@pytest.mark.unit
def test_ensure_card_selected_returns_false_after_timeout():
    """点击后始终未识别到同一行标记时应返回失败。"""
    task = make_task()
    card_area = (543, 415, 62, 54)
    clicks = []
    task.CARD_SELECTION_TIMEOUT = 0
    task.C_SELECT_CARD = SimpleNamespace(roi_front=None)
    task.I_SELECT_REALM_ON = SimpleNamespace(roi_front=(609, 313, 22, 39))
    task.appear = lambda target: True
    task.click = lambda target: clicks.append(target)
    task.screenshot = lambda: None

    assert task._ensure_card_selected(card_area) is False
    assert clicks == [task.C_SELECT_CARD]


@pytest.mark.unit
def test_reset_priority_search_switches_through_opposite_zone():
    """翻列表与好友同区服时，必须先切到对面再切回来才能真正重建列表。"""
    task = make_task()
    switched = []
    task.switch_friend_list = lambda friend: switched.append(friend)

    result = task._reset_priority_search(
        SelectFriendList.SAME_SERVER,
        SelectFriendList.SAME_SERVER,
    )

    assert result == SelectFriendList.SAME_SERVER
    assert switched == [
        SelectFriendList.DIFFERENT_SERVER,
        SelectFriendList.SAME_SERVER,
    ]


@pytest.mark.unit
def test_reset_priority_search_switches_once_across_zones():
    """翻列表与好友区服不同时，直接切过去即可，切换本身就会重建列表。"""
    task = make_task()
    switched = []
    task.switch_friend_list = lambda friend: switched.append(friend)

    result = task._reset_priority_search(
        SelectFriendList.DIFFERENT_SERVER,
        SelectFriendList.SAME_SERVER,
    )

    assert result == SelectFriendList.SAME_SERVER
    assert switched == [SelectFriendList.SAME_SERVER]


@pytest.mark.unit
def test_goto_priority_zone_skips_switch_in_same_zone():
    """遍历优先名称时同区服不切换，输入框由 I_NAME_DELETE 负责清空。"""
    task = make_task()
    switched = []
    task.switch_friend_list = lambda friend: switched.append(friend)

    result = task._goto_priority_zone(
        SelectFriendList.SAME_SERVER,
        SelectFriendList.SAME_SERVER,
    )

    assert result == SelectFriendList.SAME_SERVER
    assert switched == []


@pytest.mark.unit
def test_goto_priority_zone_switches_once_across_zones():
    """遍历优先名称时区服不同只切一次，不绕对面。"""
    task = make_task()
    switched = []
    task.switch_friend_list = lambda friend: switched.append(friend)

    result = task._goto_priority_zone(
        SelectFriendList.SAME_SERVER,
        SelectFriendList.DIFFERENT_SERVER,
    )

    assert result == SelectFriendList.DIFFERENT_SERVER
    assert switched == [SelectFriendList.DIFFERENT_SERVER]


@pytest.mark.unit
def test_priority_names_switch_zone_only_when_different():
    """遍历配置名称时只在区服变化处切换，同区服连续搜索不再切区服。"""
    task = make_task()
    switched = []
    task.switch_friend_list = lambda friend: switched.append(friend)
    task._open_priority_name_search = lambda: False
    names = [
        (SelectFriendList.SAME_SERVER, '角色甲'),
        (SelectFriendList.SAME_SERVER, '角色乙'),
        (SelectFriendList.DIFFERENT_SERVER, '角色丙'),
    ]

    selected, current_friend = task._select_from_priority_names(
        names,
        SelectFriendList.SAME_SERVER,
    )

    assert selected is False
    assert current_friend == SelectFriendList.DIFFERENT_SERVER
    # 首个名称已在入参区服、第二个同区服，均不切换；只有第三个跨区才切一次
    assert switched == [SelectFriendList.DIFFERENT_SERVER]


@pytest.mark.unit
def test_focus_priority_name_input_clicks_before_checking_placeholder(monkeypatch):
    """即使首次 OCR 为空，也必须先实际点击一次输入框。"""
    task = make_task()
    clicks = []
    task.O_NAME_CHECK = SimpleNamespace(roi=(224, 170, 266, 38), name='NAME_CHECK')
    task.device = SimpleNamespace(
        click=lambda x, y, control_name: clicks.append((x, y, control_name))
    )
    task.screenshot = lambda: None
    task._priority_name_check_texts = lambda: []
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    assert task._focus_priority_name_input() is True
    assert clicks == [(357, 189, 'NAME_CHECK')]


@pytest.mark.unit
def test_click_priority_search_repeats_at_same_coordinate(monkeypatch):
    """搜索按钮应在同一坐标连续点击两到三次。"""
    task = make_task()
    clicks = []
    task.I_SERACH_ON = SimpleNamespace(
        name='SERACH_ON',
        front_center=lambda: (576, 189),
    )
    task.device = SimpleNamespace(
        click=lambda x, y, control_name: clicks.append((x, y, control_name))
    )
    task.screenshot = lambda: None
    task.appear = lambda target: True
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.random.randint', lambda start, end: 3)
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    assert task._click_priority_search() is True
    assert clicks == [(576, 189, 'SERACH_ON')] * 3


@pytest.mark.unit
def test_select_priority_name_area_restores_template_roi(monkeypatch):
    """检查指定行后必须恢复共享模板的原始识别区域。"""
    task = make_task()
    clicks = []
    selected_states = iter((False, True))
    original_roi = (227, 139, 415, 474)
    task.I_SELECT_REALM_ON = SimpleNamespace(roi_back=original_roi)
    task.device = SimpleNamespace(
        click=lambda x, y, control_name: clicks.append((x, y, control_name))
    )
    task.screenshot = lambda: None
    task.appear = lambda target: next(selected_states)
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    assert task._select_priority_name_area('瑶光', (325, 252, 108, 33)) is True
    assert clicks == [(379, 268, 'priority_search_name')]
    assert task.I_SELECT_REALM_ON.roi_back == original_roi


@pytest.mark.unit
def test_select_from_priority_names_checks_duplicate_names_in_order():
    """第一个同名角色不满足时应继续检查第二个同名角色。"""
    task = make_task()
    first_area = (325, 252, 108, 33)
    second_area = (325, 359, 108, 33)
    checked = []
    task.switch_friend_list = lambda friend: True
    task._open_priority_name_search = lambda: True
    task._focus_priority_name_input = lambda: True
    task._input_priority_name = lambda name: True
    task._click_priority_search = lambda: True
    task._wait_priority_name_areas = lambda name: [first_area, second_area]
    task._select_priority_name_area = lambda name, area: checked.append(('name', area)) or True
    task._select_priority_name_card = lambda name, area, check_min=True: checked.append(('card', area)) or area == second_area
    task._enter_realm_and_utilize = lambda shikigami_class, shikigami_order: 'ok'

    selected, current_friend = task._select_from_priority_names(
        [(SelectFriendList.SAME_SERVER, '瑶光')],
        SelectFriendList.SAME_SERVER,
    )

    assert selected is True
    assert current_friend == SelectFriendList.SAME_SERVER
    assert checked == [
        ('name', first_area),
        ('card', first_area),
        ('name', second_area),
        ('card', second_area),
    ]


@pytest.mark.unit
def test_select_priority_name_card_accepts_matching_resource(monkeypatch):
    """指定行命中目标卡且详情 OCR 有效时应返回成功。"""
    task = make_task()

    class FakeTarget:
        """只返回一个太鼓模板匹配的假资源。"""

        threshold = 0.8
        roi_back = (541, 183, 75, 398)

        def match_all_any(self, image, threshold, roi, nms_threshold):
            """记录目标行内固定的一张模板匹配。"""
            self.roi_back = roi
            return [(0.95, 543, 253, 62, 54)]

    target = FakeTarget()
    task.__dict__['order_targets'] = SimpleNamespace(images=[target])
    task.__dict__['order_cards'] = [CardClass.TAIKO6]
    task.device = SimpleNamespace(image=object())
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(
                priority_search_min_fish=0,
                priority_search_min_taiko=0,
            )
        )
    )
    task.C_SELECT_CARD = SimpleNamespace(roi_front=None)
    task.screenshot = lambda: None
    selected = []
    task._ensure_card_selected = lambda area: selected.append(area) or True
    task.check_card_num = lambda: ('太鼓', 76)
    monkeypatch.setattr(
        'tasks.KekkaiUtilize.script_task.target_to_card_class',
        lambda matched: CardClass.TAIKO6,
    )

    assert task._select_priority_name_card('瑶光', (325, 252, 108, 33)) is True
    assert selected == [(543, 253, 62, 54)]
    assert target.roi_back == (541, 183, 75, 398)


@pytest.mark.unit
def test_perform_swipe_action_uses_fast_overlapping_gesture(monkeypatch):
    """好友列表滑动应使用一秒手势并保留行重叠。"""
    task = make_task()
    swipes = []
    clear_count = []
    values = iter((400, 540))
    task.device = SimpleNamespace(
        swipe_adb=lambda start, end, duration: swipes.append((start, end, duration)),
        click_record_clear=lambda: clear_count.append(True),
    )
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.random.randint', lambda start, end: next(values))

    task.perform_swipe_action()

    assert swipes == [((400, 540), (400, 180), 1.0)]
    assert clear_count == [True]


@pytest.mark.unit
def test_run_utilize_skips_second_realm_entry_when_select_already_utilized():
    """选卡阶段内部已寄养（搜索优先好友路径）时，不应再进一次结界。"""
    task = make_task()
    task.first_utilize = False
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(priority_search_names='同区:瑶光')
        )
    )
    task.switch_friend_list = lambda friend: True
    task._select_from_priority_names = lambda names, current, shikigami_class, shikigami_order: (
        False, SelectFriendList.SAME_SERVER)
    task._reset_priority_search = lambda current, target: target

    def fake_select(shikigami_class, shikigami_order, list_friend=None):
        # 模拟内部走搜索优先好友路径完成寄养
        task.utilized_in_select = True
        return True

    task._select_optimal_resource_card = fake_select
    task._enter_realm_and_utilize = lambda shikigami_class, shikigami_order: pytest.fail(
        '选卡阶段已寄养，不应再进入结界')

    assert task.run_utilize(friend=SelectFriendList.SAME_SERVER) is True


@pytest.mark.unit
def test_run_utilize_enters_realm_when_select_only_picked_card():
    """选卡阶段只选中卡未寄养时，仍须由 run_utilize 进结界上式神。"""
    task = make_task()
    task.first_utilize = False
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(priority_search_names='')
        )
    )
    task.switch_friend_list = lambda friend: True
    entered = []
    task._select_optimal_resource_card = lambda shikigami_class, shikigami_order, list_friend=None: True
    task._enter_realm_and_utilize = lambda shikigami_class, shikigami_order: (
        entered.append(True) or 'ok')

    assert task.run_utilize(friend=SelectFriendList.SAME_SERVER) is True
    assert entered == [True]


@pytest.mark.unit
@pytest.mark.parametrize('texts, expected', [
    (['一'], ''),
    (['1'], ''),
    (['|'], ''),
    (['请输入好友昵称或备注'], '请输入好友昵称或备注'),
    (['js15瑤光'], 'js15瑶光'),
    (['一', 'js15瑤光'], 'js15瑶光'),
])
def test_priority_input_text_drops_cursor_noise(texts, expected):
    """输入光标被 OCR 误识别成单字符时应作为噪声丢弃，多字符文本保留。"""
    from tasks.KekkaiUtilize.script_task import ScriptTask

    assert ScriptTask._priority_input_text(texts) == expected


@pytest.mark.unit
def test_input_priority_name_treats_cursor_noise_as_empty(monkeypatch):
    """输入框只剩光标噪声时应视为空框直接输入，不触发删除。"""
    task = make_task()
    calls = []
    task.device = SimpleNamespace(
        u2=SimpleNamespace(
            send_keys=lambda text, clear=False: calls.append(('send', text, clear)),
            set_fastinput_ime=lambda enable: calls.append(('ime', enable)),
        ),
        image=object(),
    )
    task.screenshot = lambda: None
    # 首次只读到光标噪声「一」，聚焦后读到目标名
    texts = iter((['一'], ['js15瑤光']))
    task._priority_name_check_texts = lambda: next(texts, ['js15瑤光'])
    task._clear_priority_name_input = lambda: pytest.fail('光标噪声不应触发删除')
    task._focus_priority_name_input = lambda: calls.append(('focus',)) or True
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    assert task._input_priority_name('js15瑤光') is True
    assert calls == [
        ('focus',),
        ('send', '', True),
        ('send', 'js15瑤光', False),
        ('ime', False),
    ]


@pytest.mark.unit
def test_run_utilize_falls_back_to_original_card_selection():
    """全部优先名称失败后应重置区服并调用原选卡流程。"""
    task = make_task()
    task.first_utilize = False
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(priority_search_names='同区:瑶光')
        )
    )
    calls = []
    task.switch_friend_list = lambda friend: calls.append(('switch', friend))
    task._select_from_priority_names = lambda names, current, shikigami_class, shikigami_order: (
        calls.append(('priority', names, current)) or (False, SelectFriendList.DIFFERENT_SERVER)
    )
    task._reset_priority_search = lambda current, target: (
        calls.append(('reset', current, target)) or target
    )
    task._select_optimal_resource_card = lambda shikigami_class, shikigami_order, list_friend=None: calls.append(('original',)) or False

    assert task.run_utilize(friend=SelectFriendList.SAME_SERVER) is False
    assert calls == [
        ('switch', SelectFriendList.SAME_SERVER),
        ('priority', [(SelectFriendList.SAME_SERVER, '瑶光')], SelectFriendList.SAME_SERVER),
        ('reset', SelectFriendList.DIFFERENT_SERVER, SelectFriendList.SAME_SERVER),
        ('original',),
    ]


@pytest.mark.unit
def test_run_utilize_priority_success_returns_without_original_flow():
    """优先名称成功后直接返回，不应再执行原选卡流程或重复进入结界。"""
    task = make_task()
    task.first_utilize = False
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(priority_search_names='同区:瑶光')
        )
    )
    task.switch_friend_list = lambda friend: True
    task._select_from_priority_names = lambda names, current, shikigami_class, shikigami_order: (True, current)
    task._select_optimal_resource_card = lambda *args, **kwargs: pytest.fail('优先名称成功后不应执行原选卡流程')
    task._enter_realm_and_utilize = lambda shikigami_class, shikigami_order: pytest.fail('优先搜索成功时结界已在内部进入')

    assert task.run_utilize(friend=SelectFriendList.SAME_SERVER) is True


@pytest.mark.unit
def test_meets_min_value_unset_keeps_positive():
    """未配置门槛时保持大于 0，配置后按达到门槛值判断。"""
    assert ScriptTask._meets_min_value(1, 0) is True
    assert ScriptTask._meets_min_value(0, 0) is False
    assert ScriptTask._meets_min_value(142, 143) is False
    assert ScriptTask._meets_min_value(143, 143) is True
    assert ScriptTask._meets_min_value(144, 143) is True


@pytest.mark.unit
def test_select_priority_name_card_respects_min_value_threshold(monkeypatch):
    """配置最低值后，低于门槛的结界卡不寄养，达到门槛才寄养。"""
    task = make_task()

    class FakeTarget:
        threshold = 0.8
        roi_back = (541, 183, 75, 398)

        def match_all_any(self, image, threshold, roi, nms_threshold):
            self.roi_back = roi
            return [(0.95, 543, 253, 62, 54)]

    monkeypatch.setattr(
        'tasks.KekkaiUtilize.script_task.target_to_card_class',
        lambda matched: CardClass.TAIKO6,
    )
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(
                priority_search_min_fish=143,
                priority_search_min_taiko=67,
            )
        )
    )
    task.__dict__['order_targets'] = SimpleNamespace(images=[FakeTarget()])
    task.__dict__['order_cards'] = [CardClass.TAIKO6]
    task.device = SimpleNamespace(image=object())
    task.C_SELECT_CARD = SimpleNamespace(roi_front=None)
    task.screenshot = lambda: None
    task._ensure_card_selected = lambda area: True

    # 太鼓 60 低于门槛 67，不应寄养
    task.check_card_num = lambda: ('太鼓', 60)
    assert task._select_priority_name_card('瑶光', (325, 252, 108, 33)) is False

    # 太鼓 67 达到门槛，应寄养
    task.check_card_num = lambda: ('太鼓', 67)
    assert task._select_priority_name_card('瑶光', (325, 252, 108, 33)) is True


@pytest.mark.unit
def test_select_from_priority_names_skips_occupied_and_tries_next():
    """优先角色结界坑位被占用时，应跳过该角色继续下一个。"""
    task = make_task()
    first_area = (325, 252, 108, 33)
    exits = []
    task.switch_friend_list = lambda friend: True
    task._reset_priority_search = lambda current, target: target
    task._open_priority_name_search = lambda: True
    task._focus_priority_name_input = lambda: True
    task._input_priority_name = lambda name: True
    task._click_priority_search = lambda: True
    task._wait_priority_name_areas = lambda name: [first_area]
    task._select_priority_name_area = lambda name, area: True
    task._select_priority_name_card = lambda name, area, check_min=True: True
    task._exit_friend_realm_to_utilize = lambda: exits.append(True)
    results = iter(('occupied', 'ok'))
    task._enter_realm_and_utilize = lambda shikigami_class, shikigami_order: next(results)
    names = [
        (SelectFriendList.SAME_SERVER, '角色甲'),
        (SelectFriendList.DIFFERENT_SERVER, '角色乙'),
    ]

    selected, current_friend = task._select_from_priority_names(
        names, SelectFriendList.SAME_SERVER)

    assert selected is True
    assert current_friend == SelectFriendList.DIFFERENT_SERVER
    assert exits == [True]


@pytest.mark.unit
def test_input_priority_name_uses_one_shot_input(monkeypatch):
    """输入框为空时应聚焦后一次性 send_keys 整串名称，并收起输入法。"""
    task = make_task()
    calls = []
    task.device = SimpleNamespace(
        u2=SimpleNamespace(
            send_keys=lambda text, clear=False: calls.append(('send', text, clear)),
            set_fastinput_ime=lambda enable: calls.append(('ime', enable)),
        ),
        image=object(),
    )
    task.screenshot = lambda: None
    # 首次读到占位文字（空输入框），聚焦后读到目标名
    texts = iter((['请输入好友昵称或备注'], ['瑶光']))
    task._priority_name_check_texts = lambda: next(texts, ['瑶光'])
    task._focus_priority_name_input = lambda: calls.append(('focus',)) or True
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    assert task._input_priority_name('瑶光') is True
    assert calls == [
        ('focus',),
        ('send', '', True),
        ('send', '瑶光', False),
        ('ime', False),
    ]


@pytest.mark.unit
def test_input_priority_name_skips_when_already_expected():
    """输入框已是目标角色名时直接复用，不再删除或重新输入。"""
    task = make_task()
    calls = []
    task.device = SimpleNamespace(
        u2=SimpleNamespace(
            send_keys=lambda text, clear=False: calls.append(('send', text, clear)),
        ),
        image=object(),
    )
    task.screenshot = lambda: None
    task._priority_name_check_texts = lambda: ['瑶光']
    task._clear_priority_name_input = lambda: calls.append(('clear',)) or True
    task._focus_priority_name_input = lambda: calls.append(('focus',)) or True

    assert task._input_priority_name('瑶光') is True
    assert calls == []


@pytest.mark.unit
def test_input_priority_name_deletes_stale_name_first(monkeypatch):
    """输入框残留其它角色名时应先点删除清空，再重新聚焦后输入。"""
    task = make_task()
    calls = []
    task.device = SimpleNamespace(
        u2=SimpleNamespace(
            send_keys=lambda text, clear=False: calls.append(('send', text, clear)),
            set_fastinput_ime=lambda enable: calls.append(('ime', enable)),
        ),
        image=object(),
    )
    task.screenshot = lambda: None
    # 首次读到上一个好友的残留名，清空并聚焦后读到目标名
    texts = iter((['角色甲'], ['角色乙']))
    task._priority_name_check_texts = lambda: next(texts, ['角色乙'])
    task._clear_priority_name_input = lambda: calls.append(('clear',)) or True
    task._focus_priority_name_input = lambda: calls.append(('focus',)) or True
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    assert task._input_priority_name('角色乙') is True
    assert calls == [
        ('clear',),
        ('focus',),
        ('send', '', True),
        ('send', '角色乙', False),
        ('ime', False),
    ]


@pytest.mark.unit
def test_clear_priority_name_input_clicks_until_placeholder(monkeypatch):
    """清空输入框应持续点击删除按钮，直到占位文字重新出现。"""
    task = make_task()
    clicks = []
    task.screenshot = lambda: None
    texts = iter((['角色甲'], ['请输入好友昵称或备注']))
    task._priority_name_check_texts = lambda: next(texts, ['请输入好友昵称或备注'])
    task.appear_then_click = lambda target, interval: clicks.append(target) or True
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    assert task._clear_priority_name_input() is True
    assert clicks == [task.I_NAME_DELETE]


@pytest.mark.unit
def test_check_utilize_add_exits_when_goto_utilize_fails(monkeypatch):
    """进入蹭卡界面失败时应退出本轮，不应继续执行寄养或设置下次运行。"""
    task = make_task()
    task.utilize_add_count = 0
    task.msg = []
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(
                utilize_rule=UtilizeRule.DEFAULT,
                select_friend_list=SelectFriendList.SAME_SERVER,
                shikigami_class=ShikigamiClass.N,
                shikigami_order=1,
            )
        ),
        notifier=SimpleNamespace(push=lambda **kwargs: None),
    )
    task.realm_goto_grown = lambda: None
    task.screenshot = lambda: None
    task.appear = lambda target: True  # I_UTILIZE_ADD 仍在，走到进入蹭卡界面
    task.grown_goto_utilize = lambda: False
    task.run_utilize = lambda *args, **kwargs: pytest.fail('进入蹭卡界面失败后不应执行寄养')
    task.back_guild = lambda: None
    task.goto_realm = lambda: None
    task.back_realm = lambda: None
    task.push_notify = lambda **kwargs: None
    task.set_next_run = lambda **kwargs: pytest.fail('进入蹭卡界面失败后不应设置下次运行')
    monkeypatch.setattr('tasks.KekkaiUtilize.script_task.time.sleep', lambda seconds: None)

    task.check_utilize_add()

    # 只尝试一轮，计数 +1 后因界面进入失败退出
    assert task.utilize_add_count == 1


@pytest.mark.unit
def test_select_optimal_card_uses_best_value_when_no_min_reached():
    """第一轮探索未达到最低值时，第二轮应选择当前最佳值兜底。"""
    task = make_task()
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(utilize_rule=UtilizeRule.DEFAULT)
        )
    )
    task.screenshot = lambda: None
    calls = []

    def fake_current_select_best(best_card_type=None, best_card_num=0, selected_card=False):
        calls.append((best_card_type, best_card_num, selected_card))
        if not selected_card:
            # 探索模式：整轮没有达到最低值的卡，只记录到斗鱼最佳值 80（低于门槛 143）
            task.ap_max_num = 80
            task.jade_max_num = 0
            return None
        # 确认模式：直接确认当前最佳值
        return True

    task._current_select_best = fake_current_select_best

    assert task._select_optimal_resource_card() is True
    # 探索后进入第二轮，用斗鱼最佳值 80 确认选择
    assert calls == [
        (None, 0, False),
        ('斗鱼', 80, True),
    ]


@pytest.mark.unit
def test_select_optimal_card_gives_up_when_no_card_recorded():
    """探索整轮都没记录到任何结界卡时，应放弃本轮。"""
    task = make_task()
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(utilize_rule=UtilizeRule.DEFAULT)
        )
    )
    task.screenshot = lambda: None

    def fake_current_select_best(best_card_type=None, best_card_num=0, selected_card=False):
        task.ap_max_num = 0
        task.jade_max_num = 0
        return None

    task._current_select_best = fake_current_select_best

    assert task._select_optimal_resource_card() is False


@pytest.mark.unit
def test_select_priority_name_card_records_unmet_value(monkeypatch):
    """未达最低值的结界卡应记录到优先好友数值池，供后续最佳值匹配。"""
    task = make_task()
    task.priority_friend_records = {}

    class FakeTarget:
        threshold = 0.8
        roi_back = (541, 183, 75, 398)

        def match_all_any(self, image, threshold, roi, nms_threshold):
            self.roi_back = roi
            return [(0.95, 543, 253, 62, 54)]

    monkeypatch.setattr(
        'tasks.KekkaiUtilize.script_task.target_to_card_class',
        lambda matched: CardClass.TAIKO6,
    )
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(
                priority_search_min_fish=143,
                priority_search_min_taiko=67,
            )
        )
    )
    task.__dict__['order_targets'] = SimpleNamespace(images=[FakeTarget()])
    task.__dict__['order_cards'] = [CardClass.TAIKO6]
    task.device = SimpleNamespace(image=object())
    task.C_SELECT_CARD = SimpleNamespace(roi_front=None)
    task.screenshot = lambda: None
    task._ensure_card_selected = lambda area: True

    # 太鼓 60 低于门槛 67，不寄养但记录该好友数值
    task.check_card_num = lambda: ('太鼓', 60)
    assert task._select_priority_name_card('瑶光', (325, 252, 108, 33)) is False
    assert task.priority_friend_records == {'瑶光': {'太鼓': 60}}


@pytest.mark.unit
def test_select_optimal_card_uses_matched_friend_directly():
    """最佳值恰好等于某优先好友记录的数值时，应直接搜索该好友寄养而非翻第二轮。"""
    task = make_task()
    task.priority_friend_records = {
        '瑶光': {'zone': SelectFriendList.SAME_SERVER, '斗鱼': 80},
    }
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(utilize_rule=UtilizeRule.DEFAULT)
        )
    )
    task.screenshot = lambda: None
    calls = []

    def fake_explore(best_card_type=None, best_card_num=0, selected_card=False):
        if not selected_card:
            # 探索翻列表记录斗鱼最佳值 80
            task.ap_max_num = 80
            task.jade_max_num = 0
            return None
        return pytest.fail('匹配到优先好友后不应翻第二轮确认')

    task._current_select_best = fake_explore
    task._search_priority_friend_and_utilize = (
        lambda name, friend, shikigami_class, shikigami_order, list_friend=None: (
            calls.append((name, friend, list_friend)) or True)
    )

    assert task._select_optimal_resource_card(
        list_friend=SelectFriendList.DIFFERENT_SERVER) is True
    # 最佳值 80 匹配瑶光记录 → 直接搜瑶光，不翻第二轮；翻列表区服须透传下去
    assert calls == [(
        '瑶光',
        SelectFriendList.SAME_SERVER,
        SelectFriendList.DIFFERENT_SERVER,
    )]


@pytest.mark.unit
def test_select_optimal_card_resets_list_after_friend_search_failed():
    """搜索优先好友寄养失败后，回落翻列表前必须先重置列表解除搜索过滤。"""
    task = make_task()
    task.priority_friend_records = {
        '瑶光': {'zone': SelectFriendList.DIFFERENT_SERVER, '斗鱼': 80},
    }
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(utilize_rule=UtilizeRule.DEFAULT)
        )
    )
    task.screenshot = lambda: None
    calls = []

    def fake(best_card_type=None, best_card_num=0, selected_card=False):
        calls.append(('list', best_card_type, best_card_num, selected_card))
        if not selected_card:
            # 探索记录斗鱼最佳值 80，与瑶光记录相同 → 触发搜索好友
            task.ap_max_num = 80
            task.jade_max_num = 0
            return None
        return True

    task._current_select_best = fake
    task._search_priority_friend_and_utilize = (
        lambda name, friend, shikigami_class, shikigami_order, list_friend=None: (
            calls.append(('search', name)) or False)
    )
    task._reset_priority_search = lambda current, target: (
        calls.append(('reset', current, target)) or target)

    assert task._select_optimal_resource_card(
        list_friend=SelectFriendList.SAME_SERVER) is True
    # 搜好友失败 → 先重置回翻列表区服，再翻第二轮确认最佳值
    assert calls == [
        ('list', None, 0, False),
        ('search', '瑶光'),
        ('reset', SelectFriendList.DIFFERENT_SERVER, SelectFriendList.SAME_SERVER),
        ('list', '斗鱼', 80, True),
    ]


@pytest.mark.unit
def test_select_optimal_card_falls_back_when_friend_value_lower():
    """优先好友记录值低于翻列表最佳值时，应翻第二轮确认最佳值而非搜好友。"""
    task = make_task()
    task.priority_friend_records = {
        '瑶光': {'zone': SelectFriendList.SAME_SERVER, '斗鱼': 80},
    }
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(utilize_rule=UtilizeRule.DEFAULT)
        )
    )
    task.screenshot = lambda: None
    calls = []

    def fake(best_card_type=None, best_card_num=0, selected_card=False):
        calls.append((best_card_type, best_card_num, selected_card))
        if not selected_card:
            # 翻列表最佳值 100，高于瑶光记录的 80
            task.ap_max_num = 100
            task.jade_max_num = 0
            return None
        return True

    task._current_select_best = fake
    task._search_priority_friend_and_utilize = lambda *args, **kwargs: pytest.fail('记录值低于最佳值不应搜好友')

    assert task._select_optimal_resource_card() is True
    # 瑶光 80 < 最佳值 100 → 不匹配，翻第二轮确认 100
    assert calls == [
        (None, 0, False),
        ('斗鱼', 100, True),
    ]


@pytest.mark.unit
def test_select_from_priority_names_removes_occupied_friend_record():
    """坑位被占用的好友应被移出最佳值记录，避免后续被搜索。"""
    task = make_task()
    task.priority_friend_records = {
        '瑶光': {'zone': SelectFriendList.SAME_SERVER, '斗鱼': 130},
    }
    task.switch_friend_list = lambda friend: True
    task._open_priority_name_search = lambda: True
    task._focus_priority_name_input = lambda: True
    task._input_priority_name = lambda name: True
    task._click_priority_search = lambda: True
    task._wait_priority_name_areas = lambda name: [(325, 252, 108, 33)]
    task._select_priority_name_area = lambda name, area: True
    task._select_priority_name_card = lambda name, area, check_min=True: True
    task._enter_realm_and_utilize = lambda shikigami_class, shikigami_order: 'occupied'
    task._exit_friend_realm_to_utilize = lambda: None

    selected, current_friend = task._select_from_priority_names(
        [(SelectFriendList.SAME_SERVER, '瑶光')],
        SelectFriendList.SAME_SERVER,
    )

    assert selected is False
    # 坑位占用后该好友记录被移除
    assert task.priority_friend_records == {}


@pytest.mark.unit
def test_run_utilize_does_not_swipe_when_priority_succeeds():
    """优先搜索成功时不应滑动列表（滑动只在搜索失败后执行）。"""
    task = make_task()
    task.first_utilize = True
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(priority_search_names='同区:瑶光')
        )
    )
    swipes = []
    task.swipe = lambda *args, **kwargs: swipes.append(args)
    task.switch_friend_list = lambda friend: True
    task._select_from_priority_names = lambda names, current, shikigami_class, shikigami_order: (True, current)
    task._select_optimal_resource_card = lambda *args, **kwargs: pytest.fail('优先搜索成功不应走原流程')
    task._enter_realm_and_utilize = lambda *args, **kwargs: pytest.fail('优先搜索成功不应再进入结界')

    assert task.run_utilize(friend=SelectFriendList.SAME_SERVER) is True
    # 优先搜索成功，不滑动
    assert swipes == []


@pytest.mark.unit
def test_run_utilize_swipes_after_priority_failure():
    """首次进入且优先搜索失败后，才滑动列表到底部再走原流程。"""
    task = make_task()
    task.first_utilize = True
    task.config = SimpleNamespace(
        kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(priority_search_names='同区:瑶光')
        )
    )
    swipes = []
    switched = []
    task.swipe = lambda *args, **kwargs: swipes.append(args)
    task.switch_friend_list = lambda friend: switched.append(friend) or True
    task._select_from_priority_names = lambda names, current, shikigami_class, shikigami_order: (False, current)
    task._reset_priority_search = lambda current, target: target
    task._select_optimal_resource_card = lambda shikigami_class, shikigami_order, list_friend=None: True
    task._enter_realm_and_utilize = lambda shikigami_class, shikigami_order: 'ok'

    assert task.run_utilize(friend=SelectFriendList.SAME_SERVER) is True
    # 搜索失败后滑动一次（S_U_END 滑到底）
    assert len(swipes) == 1
    # 拉到底后切换一遍同区跨区，刷新列表；搜索前不做区服切换
    assert switched == [SelectFriendList.DIFFERENT_SERVER, SelectFriendList.SAME_SERVER]
