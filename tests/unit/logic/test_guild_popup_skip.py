# This Python file uses the following encoding: utf-8
"""寮页面突发弹窗（「已收到碎片」/「感谢」）统一跳过逻辑的单元测试。

不依赖真实设备与图像：用假宿主类承接 mixin 的 super() 链，
只验证控制流——激活范围内检测弹窗点击红键、消失后停止、顽固弹窗
限次放弃，以及未激活（非寮子任务）时零检测零点击。
"""
import types

import pytest

from tasks.DailyAltAcc.assets import DailyAltAccAssets
from tasks.DailyAltAcc.utils import GuildPopupMixin, guild_popup_guard


class _FakeBase:
    """扮演 BaseTask：提供 mixin super() 链所需的截图与匹配原语。

    匹配行为由用例通过 _popup_visible 注入；appear_then_click 只记录
    (目标, 点击区域) 不改变画面，弹窗状态变化由用例自行驱动。
    """

    def __init__(self):
        self.clicks = []          # 记录 appear_then_click 的 (target, action)
        self.shot_count = 0       # device.screenshot() 触发次数
        self._popup_visible = lambda target: False
        self.device = types.SimpleNamespace(screenshot=self._device_shot)

    def _device_shot(self):
        self.shot_count += 1

    def screenshot(self):
        # 模拟 BaseTask.screenshot：落到 device 取帧
        self.device.screenshot()
        return None

    def appear(self, target, interval=None, threshold=None):
        return self._popup_visible(target)

    def appear_then_click(self, target, action=None, interval=None, threshold=None):
        self.clicks.append((target, action))
        return self._popup_visible(target)


class _Host(GuildPopupMixin, _FakeBase):
    """最小宿主：mixin 在 MRO 中位于假基类之前，screenshot 覆盖生效。"""


def _make_host(popup_visible) -> _Host:
    """构造激活态宿主：寮子任务流程内的截图行为。"""
    host = _Host()
    host._popup_visible = popup_visible
    host._guild_popup_active = True
    return host


@pytest.mark.unit
def test_no_popup_no_extra_click():
    """激活范围内无弹窗时 screenshot() 透传：只截一次图，不产生任何点击。"""
    host = _make_host(lambda target: False)

    host.screenshot()

    assert host.clicks == []
    assert host.shot_count == 1


@pytest.mark.unit
def test_popup_dismissed_after_one_click():
    """激活范围内出现「已收到碎片」弹窗时应点击红键，确认消失后即停止。"""
    host = _make_host(lambda target: False)
    state = {'visible': True}
    # 只有 I_SUDDEN 弹窗可见
    host._popup_visible = lambda target: state['visible'] and target is DailyAltAccAssets.I_SUDDEN

    def click_and_close(target, action=None, interval=None, threshold=None):
        host.clicks.append((target, action))
        if target is DailyAltAccAssets.I_SUDDEN:
            # 点击红键后弹窗消失
            state['visible'] = False
        return True

    host.appear_then_click = click_and_close

    host.screenshot()

    assert host.clicks == [(DailyAltAccAssets.I_SUDDEN, DailyAltAccAssets.C_BACK_RED)]
    # super 链截图 1 次 + 关闭循环确认帧 1 次
    assert host.shot_count == 2


@pytest.mark.unit
def test_stubborn_popup_gives_up_after_limit():
    """弹窗点击后仍不消失时最多点击 GUILD_POPUP_MAX_CLICKS 次，不死循环。"""
    # 「感谢」弹窗始终可见（顽固弹窗）
    host = _make_host(lambda target: target is DailyAltAccAssets.I_PAGE_TK)

    host.screenshot()

    page_tk_clicks = [c for c in host.clicks if c[0] is DailyAltAccAssets.I_PAGE_TK]
    assert len(page_tk_clicks) == host.GUILD_POPUP_MAX_CLICKS
    # 每次点击都应作用于红键
    assert all(action is DailyAltAccAssets.C_BACK_RED for _, action in page_tk_clicks)
    # super 链 1 次 + 每次点击后的确认帧 5 次
    assert host.shot_count == 1 + host.GUILD_POPUP_MAX_CLICKS


@pytest.mark.unit
def test_inactive_scope_skips_detection():
    """非寮子任务（未激活）时即使弹窗在画面上也不检测、不点击。"""
    host = _Host()
    # 弹窗可见但未激活（_guild_popup_active 默认 False）
    host._popup_visible = lambda target: target is DailyAltAccAssets.I_SUDDEN

    host.screenshot()

    assert host.clicks == []
    assert host.shot_count == 1


@pytest.mark.unit
def test_scope_restores_previous_state():
    """guild_popup_scope 退出（含异常）后应恢复进入前的激活状态。"""
    host = _Host()

    with host.guild_popup_scope():
        assert host._guild_popup_active is True
    assert host._guild_popup_active is False

    # 异常路径同样要复位
    with pytest.raises(RuntimeError):
        with host.guild_popup_scope():
            raise RuntimeError('boom')
    assert host._guild_popup_active is False


@pytest.mark.unit
def test_guard_decorator_scopes_entry_method():
    """@guild_popup_guard 装饰的子任务入口：流程内激活，退出后恢复。"""
    observed = []

    class _Task(_Host):
        @guild_popup_guard
        def run_guild_thing(self):
            observed.append(self._guild_popup_active)
            # 流程内的截图走检测路径（弹窗不可见，仅验证激活标志被读到）
            self.screenshot()

    task = _Task()
    task.run_guild_thing()

    assert observed == [True]
    assert task._guild_popup_active is False
    assert task.clicks == []


@pytest.mark.unit
def test_mro_wires_mixin_into_task_chains():
    """各寮相关任务链的 MRO 中 mixin 应先于 BaseTask 生效。"""
    from tasks.DailyAltAcc.utils import DailyAltAccBase
    from tasks.ReturnGift.script_task import ScriptTask as ReturnGiftScriptTask

    for cls in (DailyAltAccBase, ReturnGiftScriptTask):
        mro_names = [c.__name__ for c in cls.__mro__]
        assert mro_names.index('GuildPopupMixin') < mro_names.index('BaseTask'), \
            f'{cls.__name__} 的 screenshot() 未被 GuildPopupMixin 覆盖'

    # ReturnGift 整个任务都在寮页面体系，弹窗跳过应常开
    assert ReturnGiftScriptTask._guild_popup_active is True
    # DailyAltAcc 各子任务混跑，默认不激活、由入口装饰器划作用域
    assert DailyAltAccBase._guild_popup_active is False
    # KekkaiUtilize / KekkaiActivation（蹭卡/挂卡）按需求不启用弹窗跳过
    from tasks.KekkaiUtilize.script_task import ScriptTask as KekkaiUtilizeTask
    from tasks.KekkaiActivation.script_task import ScriptTask as KekkaiActivationTask
    assert 'GuildPopupMixin' not in [c.__name__ for c in KekkaiUtilizeTask.__mro__]
    assert 'GuildPopupMixin' not in [c.__name__ for c in KekkaiActivationTask.__mro__]
