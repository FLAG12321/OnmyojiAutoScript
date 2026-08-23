# This Python file uses the following encoding: utf-8
"""普通勾协双目标 OCR 的解析、触发范围和汇总测试。"""

from types import SimpleNamespace

import pytest

from module.atom.ocr import RuleOcr
from tasks.DailyAltAcc.config import MSGType
from tasks.DailyAltAcc.cooperation import Cooperation, _parse_cooperation_monster
from tasks.MultiDailyAltAcc.script_task import ScriptTask
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationType


pytestmark = pytest.mark.unit


def _coop(monkeypatch, *active):
    coop = object.__new__(Cooperation)
    coop.msg = []
    coop.device = SimpleNamespace(image=object())
    active_ids = {id(item) for item in active}
    monkeypatch.setattr(Cooperation, "screenshot", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        Cooperation,
        "appear",
        lambda self, item, interval=None: id(item) in active_ids,
    )
    return coop


@pytest.mark.parametrize(
    ("raw_text", "prefix", "expected"),
    [
        ("自己击败青蛙瓷器", "自己击败", "青蛙瓷器"),
        ("好友击败雨女", "好友击败", "雨女"),
        ("自己击败青蛙瓷器 2/2", "自己击败", "青蛙瓷器"),
        ("好友击败雨女0/2", "好友击败", "雨女"),
        (" 自己击败 饿鬼 ", "自己击败", "饿鬼"),
        ("好友击败武士之灵0／3", "好友击败", "武士之灵"),
    ],
)
def test_parse_cooperation_monster(raw_text, prefix, expected):
    assert _parse_cooperation_monster(raw_text, prefix) == expected


def test_cooperation_target_rois_are_fixed_by_slot():
    expected = {
        1: ((180, 403, 200, 30), (180, 448, 200, 30)),
        2: ((480, 403, 200, 30), (480, 448, 200, 30)),
        3: ((780, 403, 200, 30), (780, 448, 200, 30)),
    }
    for slot, (discoverer_roi, friend_roi) in expected.items():
        assert getattr(
            WantedQuestsAssets, f"O_WQ_COOPERATION_DISCOVERER_{slot}"
        ).roi == list(discoverer_roi)
        assert getattr(
            WantedQuestsAssets, f"O_WQ_COOPERATION_FRIEND_{slot}"
        ).roi == list(friend_roi)


def test_normal_jade_reads_both_targets_and_keeps_order(monkeypatch):
    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_INVITE_1,
        WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_1,
    )

    ocr_result = {
        "WQ_COOPERATION_DISCOVERER_1": "自己击败青蛙瓷器",
        "WQ_COOPERATION_FRIEND_1": "好友击败雨女",
    }
    monkeypatch.setattr(RuleOcr, "ocr", lambda self, image: ocr_result.get(self.name, ""))

    result = coop.get_cooperation_info()

    assert result[0]["discoverer_monster"] == "青蛙瓷器"
    assert result[0]["friend_monster"] == "雨女"
    assert result[0]["monster_text"] == "青蛙瓷器&雨女"
    assert coop.msg == [[
        MSGType.cooperation,
        {
            "type": "jade",
            "real": False,
            "label": "普通勾协",
            "discoverer_monster": "青蛙瓷器",
            "friend_monster": "雨女",
            "monster_text": "青蛙瓷器&雨女",
        },
    ]]


@pytest.mark.parametrize(
    "active_type",
    [
        "I_WQ_COOPERATION_TYPE_SUSHI_1",
        "I_WQ_COOPERATION_TYPE_GOLD_1",
    ],
)
def test_non_jade_does_not_call_target_ocr(monkeypatch, active_type):
    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_INVITE_1,
        getattr(WantedQuestsAssets, active_type),
    )

    def fail_if_called(self, image):
        raise AssertionError("target OCR must not run for non-jade cooperation")

    monkeypatch.setattr(RuleOcr, "ocr", fail_if_called)
    coop.get_cooperation_info()


def test_real_jade_does_not_call_target_ocr(monkeypatch):
    from tasks.DailyAltAcc.assets import DailyAltAccAssets

    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_INVITE_1,
        WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_1,
        DailyAltAccAssets.I_REAL_FLAG_1,
    )

    def fail_if_called(self, image):
        raise AssertionError("target OCR must not run for real-world cooperation")

    monkeypatch.setattr(RuleOcr, "ocr", fail_if_called)
    result = coop.get_cooperation_info()

    assert "discoverer_monster" not in result[0]
    assert coop.msg[0][1] == {"type": "jade", "real": True, "label": "现世勾协"}


def test_ocr_failure_keeps_original_normal_jade_event(monkeypatch):
    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_INVITE_1,
        WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_1,
    )
    monkeypatch.setattr(RuleOcr, "ocr", lambda self, image: "")

    result = coop.get_cooperation_info()

    assert result[0] == {
        "type": CooperationType.Jade,
        "inviteBtn": WantedQuestsAssets.I_WQ_INVITE_1,
        "real": False,
    }
    assert coop.msg == [[
        MSGType.cooperation,
        {"type": "jade", "real": False, "label": "普通勾协"},
    ]]


def test_normal_jade_target_is_added_to_existing_summary_format():
    text = ScriptTask._build_summary_content([
        {
            "type": "jade",
            "real": False,
            "character": "Val2号",
            "svr": "常世之国",
            "apple_or_android": True,
            "monster_text": "涂壁&管狐",
        },
    ])

    assert "• 涂壁&管狐：Val2号（常世之国｜安卓）" in text
