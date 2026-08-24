# This Python file uses the following encoding: utf-8
"""普通勾协双目标 OCR 的解析、触发范围和汇总测试。"""

from types import SimpleNamespace

import pytest

from module.atom.ocr import RuleOcr
from tasks.DailyAltAcc.config import MSGType
from tasks.DailyAltAcc.cooperation import (
    Cooperation,
    REAL_COOPERATION_ANCHOR_REFERENCE,
    _parse_cooperation_monster,
    _parse_real_cooperation_monster,
)
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


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("击败2个白狼", "白狼"),
        ("击败 2 个 荒川之主", "荒川之主"),
        ("击败2个荒川之主0/2", "荒川之主"),
        ("击败2个", ""),
        ("白狼", ""),
    ],
)
def test_parse_real_cooperation_monster(raw_text, expected):
    assert _parse_real_cooperation_monster(raw_text) == expected


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

    expected_real = {
        1: (180, 425, 200, 32),
        2: (480, 425, 200, 32),
    }
    for slot, roi in expected_real.items():
        assert getattr(
            WantedQuestsAssets, f"O_WQ_REAL_COOPERATION_MONSTER_{slot}"
        ).roi == list(roi)


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
        "I_WQ_COOPERATION_TYPE_GOLD_1",
        "I_WQ_COOPERATION_TYPE_DOG_FOOD_1",
        "I_WQ_COOPERATION_TYPE_CAT_FOOD_1",
    ],
)
def test_non_target_cooperation_does_not_call_target_ocr(monkeypatch, active_type):
    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_INVITE_1,
        getattr(WantedQuestsAssets, active_type),
    )

    def fail_if_called(self, image):
        raise AssertionError("target OCR must not run for this cooperation type")

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


def test_normal_sushi_reads_both_targets_with_fast_path(monkeypatch):
    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_INVITE_1,
        WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI_1,
    )
    called = []
    ocr_result = {
        "WQ_COOPERATION_DISCOVERER_1": "自己击败青蛙瓷器",
        "WQ_COOPERATION_FRIEND_1": "好友击败雨女",
    }

    def fake_ocr(self, image):
        called.append(self.name)
        return ocr_result.get(self.name, "")

    monkeypatch.setattr(RuleOcr, "ocr", fake_ocr)
    result = coop.get_cooperation_info()

    assert result[0]["type"] == CooperationType.Sushi
    assert result[0]["real"] is False
    assert result[0]["monster_text"] == "青蛙瓷器&雨女"
    assert coop.msg[0][1]["monster_text"] == "青蛙瓷器&雨女"
    assert called == ["WQ_COOPERATION_DISCOVERER_1", "WQ_COOPERATION_FRIEND_1"]


def test_normal_sushi_retries_once_with_invite_anchor_shift(monkeypatch):
    invite = WantedQuestsAssets.I_WQ_INVITE_1
    coop = _coop(monkeypatch, invite, WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI_1)
    monkeypatch.setattr(invite, "roi_front", [147, 368, 39, 47])
    calls = []
    ocr_result = {
        "WQ_COOPERATION_DISCOVERER_FALLBACK_1": "自己击败青蛙瓷器",
        "WQ_COOPERATION_FRIEND_FALLBACK_1": "好友击败雨女",
    }

    def fake_ocr(self, image):
        calls.append((self.name, tuple(self.roi)))
        return ocr_result.get(self.name, "")

    monkeypatch.setattr(RuleOcr, "ocr", fake_ocr)
    result = coop.get_cooperation_info()

    assert result[0]["monster_text"] == "青蛙瓷器&雨女"
    assert calls == [
        ("WQ_COOPERATION_DISCOVERER_1", (180, 403, 200, 30)),
        ("WQ_COOPERATION_FRIEND_1", (180, 448, 200, 30)),
        ("WQ_COOPERATION_DISCOVERER_FALLBACK_1", (190, 410, 200, 30)),
        ("WQ_COOPERATION_FRIEND_FALLBACK_1", (190, 455, 200, 30)),
    ]


def test_normal_sushi_second_failure_keeps_original_event(monkeypatch):
    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_INVITE_1,
        WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI_1,
    )
    monkeypatch.setattr(RuleOcr, "ocr", lambda self, image: "")

    result = coop.get_cooperation_info()

    assert result[0] == {
        "type": CooperationType.Sushi,
        "inviteBtn": WantedQuestsAssets.I_WQ_INVITE_1,
        "real": False,
    }
    assert coop.msg[0][1] == {"type": "sushi", "real": False, "label": "普通体协"}


def test_real_sushi_reads_one_target_with_fast_path(monkeypatch):
    from tasks.DailyAltAcc.assets import DailyAltAccAssets

    flag = DailyAltAccAssets.I_REAL_FLAG_1
    coop = _coop(
        monkeypatch,
        WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI_1,
        flag,
    )
    monkeypatch.setattr(flag, "roi_front", [159, 293, 29, 31])
    called = []

    def fake_ocr(self, image):
        called.append(self.name)
        return "击败2个白狼" if self.name == "WQ_REAL_COOPERATION_MONSTER_1" else ""

    monkeypatch.setattr(RuleOcr, "ocr", fake_ocr)
    result = coop.get_cooperation_info()

    assert result[0]["type"] == CooperationType.Sushi
    assert result[0]["real"] is True
    assert result[0]["monster_text"] == "白狼"
    assert coop.msg[0][1]["monster_text"] == "白狼"
    assert called == ["WQ_REAL_COOPERATION_MONSTER_1"]


def test_real_sushi_retries_once_with_real_flag_anchor_shift(monkeypatch):
    from tasks.DailyAltAcc.assets import DailyAltAccAssets

    flag = DailyAltAccAssets.I_REAL_FLAG_1
    coop = _coop(monkeypatch, WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI_1, flag)
    reference = REAL_COOPERATION_ANCHOR_REFERENCE[0]
    monkeypatch.setattr(flag, "roi_front", [reference[0] + 8, reference[1] + 5, 29, 31])
    calls = []

    def fake_ocr(self, image):
        calls.append((self.name, tuple(self.roi)))
        return "击败2个白狼" if self.name == "WQ_REAL_COOPERATION_MONSTER_FALLBACK_1" else ""

    monkeypatch.setattr(RuleOcr, "ocr", fake_ocr)
    result = coop.get_cooperation_info()

    assert result[0]["monster_text"] == "白狼"
    assert calls == [
        ("WQ_REAL_COOPERATION_MONSTER_1", (180, 425, 200, 32)),
        ("WQ_REAL_COOPERATION_MONSTER_FALLBACK_1", (188, 430, 200, 32)),
    ]


def test_real_sushi_second_failure_keeps_original_event(monkeypatch):
    from tasks.DailyAltAcc.assets import DailyAltAccAssets

    flag = DailyAltAccAssets.I_REAL_FLAG_1
    coop = _coop(monkeypatch, WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI_1, flag)
    monkeypatch.setattr(flag, "roi_front", [159, 293, 29, 31])
    monkeypatch.setattr(RuleOcr, "ocr", lambda self, image: "")

    result = coop.get_cooperation_info()

    assert result[0] == {
        "type": CooperationType.Sushi,
        "inviteBtn": WantedQuestsAssets.I_WQ_INVITE_1,
        "real": True,
    }
    assert coop.msg[0][1] == {"type": "sushi", "real": True, "label": "现世体协"}


def test_real_sushi_index_three_does_not_run_monster_ocr(monkeypatch):
    from tasks.DailyAltAcc.assets import DailyAltAccAssets

    coop = _coop(
        monkeypatch,
        DailyAltAccAssets.I_REAL_FLAG_1,
        DailyAltAccAssets.I_REAL_FLAG_2,
        DailyAltAccAssets.I_REAL_FLAG_3,
        WantedQuestsAssets.I_WQ_COOPERATION_TYPE_SUSHI_3,
    )

    def fail_if_called(self, image):
        raise AssertionError("real-world index three must not run monster OCR")

    monkeypatch.setattr(RuleOcr, "ocr", fail_if_called)
    result = coop.get_cooperation_info()

    assert result == [{
        "type": CooperationType.Sushi,
        "inviteBtn": WantedQuestsAssets.I_WQ_INVITE_3,
        "real": True,
    }]
    assert coop.msg == [[
        MSGType.cooperation,
        {"type": "sushi", "real": True, "label": "现世体协"},
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


@pytest.mark.parametrize("real", [False, True])
def test_sushi_target_is_added_to_existing_summary_format(real):
    text = ScriptTask._build_summary_content([
        {
            "type": "sushi",
            "real": real,
            "character": "Val2号",
            "svr": "常世之国",
            "apple_or_android": True,
            "monster_text": "青蛙瓷器&雨女" if not real else "白狼",
        },
    ])

    expected = "青蛙瓷器&雨女" if not real else "白狼"
    assert f"• {expected}：Val2号（常世之国｜安卓）" in text
