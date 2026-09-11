# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

from module.atom.image import RuleImage
from module.base.decorator import del_cached_property
from module.base.timer import Timer
from module.logger import logger

from tasks.Component.Costume.config import (MainType, CostumeConfig, RealmType,
                                            ThemeType, ShikigamiType, SignType, BattleType, CarpBannerType)
from tasks.Component.Costume.assets import CostumeAssets
from tasks.Component.CostumeTheme.assets import CostumeThemeAssets
from tasks.Component.CostumeBattle.assets import CostumeBattleAssets
from tasks.Component.CostumeShikigami.assets import CostumeShikigamiAssets
from tasks.Component.CostumeCarpBanner.assets import CostumeCarpBannerAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.Pets.assets import PetsAssets
from tasks.Restart.assets import RestartAssets

# 庭院皮肤
# 主界面皮肤（使用字典推导式动态生成）
# key 用 (资产类, 属性名) 而不是裸字符串：替换直接落到类属性对象上，不依赖调用方任务的
# MRO，也不会因为「碰巧没有别的任务持有该属性」而静默跳过。
# I_PET_HOUSE 要显式指向 PetsAssets——GameUiAssets 上没有这个属性，旧代码是靠
# hasattr(self, ...) 作用在 Pets 任务实例上才换到的（见本任务「关键设计选择」）。
main_costume_model = {
    getattr(MainType, f"COSTUME_MAIN_{i}"): {
        (GameUiAssets, 'I_CHECK_MAIN'): f'I_CHECK_MAIN_{i}',
        (GameUiAssets, 'I_MAIN_GOTO_EXPLORATION'): f'I_MAIN_GOTO_EXPLORATION_{i}',
        (GameUiAssets, 'I_MAIN_GOTO_SUMMON'): f'I_MAIN_GOTO_SUMMON_{i}',
        (GameUiAssets, 'I_MAIN_GOTO_TOWN'): f'I_MAIN_GOTO_TOWN_{i}',
        (PetsAssets, 'I_PET_HOUSE'): f'I_PET_HOUSE_{i}',
    } for i in range(1, 15)
}


# 鲤鱼旗皮肤
carpbanner_costume_model = {
    getattr(CarpBannerType, f"COSTUME_CARPBANNER_{i}"): {
        'I_SHI_CARD': f'I_SHI_CARD_{i}',
        'I_SHI_DEFENSE': f'I_SHI_DEFENSE_{i}',
        'I_SHI_GROWN': f'I_SHI_GROWN_{i}',
    } for i in range(1, 4)
}


# 战斗主题（使用循环处理常规情况 + 特例处理）
battle_theme_model = {}
for i in range(1, 14):
    entry = {
        'I_LOCAL': f'I_LOCAL_{i}',
        'I_EXIT': f'I_EXIT_{i}',
        'I_FRIENDS': f'I_FRIENDS_{i}',
    }
    if i == 8:  # 特殊处理第8项
        entry.update({
            'I_WIN': 'I_WIN_8',
            'I_DE_WIN': 'I_DE_WIN_8',
            'I_FALSE': 'I_FALSE_8'
        })
    if i == 12:  # 特殊处理第12项
        entry.update({
            'I_WIN': 'I_WIN_12',
            'I_DE_WIN': 'I_DE_WIN_12',
            'I_FALSE': 'I_FALSE_12'
        })
    if i == 13:  # 13项特殊处理
        entry.update({
            'I_WIN': 'I_WIN_13',
            'I_DE_WIN': 'I_DE_WIN_13',
            'I_FALSE': 'I_FALSE_13'
        })
    battle_theme_model[getattr(BattleType, f"COSTUME_BATTLE_{i}")] = entry

# 幕间主题
shikigami_costume_model = {
    getattr(ShikigamiType, f"COSTUME_SHIKIGAMI_{i}"): {
        # GameUi 进出式神录
        'I_CHECK_RECORDS': f'I_CHECK_RECORDS_{i}',
        'I_RECORD_SOUL_BACK': f'I_RECORD_SOUL_BACK_{i}',
        # SwitchSoul 相关
        'I_SOUL_PRESET': f'I_SOUL_PRESET_{i}',
        'I_SOU_CHECK_IN': f'I_SOU_CHECK_IN_{i}',
        'I_SOU_TEAM_PRESENT': f'I_SOU_TEAM_PRESENT_{i}',
        'I_SOU_CLICK_PRESENT': f'I_SOU_CLICK_PRESENT_{i}',
        'I_SOU_SWITCH_SURE': f'I_SOU_SWITCH_SURE_{i}',
        # SwitchSoul 分组相关 (1-7组)
        **{f'I_SOU_CHECK_GROUP_{g}': f'I_SOU_CHECK_GROUP_{g}_{i}' for g in range(1, 8)},
        # SwitchSoul 队伍相关 (1-4队)
        **{f'I_SOU_SWITCH_{t}': f'I_SOU_SWITCH_{t}_{i}' for t in range(1, 5)},
        # SoulsTidy 相关
        'I_ST_SOULS': f'I_ST_SOULS_{i}',
        'I_ST_REPLACE': f'I_ST_REPLACE_{i}',
    }
    for i in range(1, 11)  # 目前支持 COSTUME_SHIKIGAMI_1 到 COSTUME_SHIKIGAMI_10
}

def _clear_rule_image_cache(obj: RuleImage) -> None:
    """清掉 RuleImage 的懒加载缓存。

    replace_img 只改 .file，而 load_image() 开头 `if self._image is not None: return`
    会直接早退，不清就是换了白换；name / kp / des 都是 cached_property（结果塞在实例
    __dict__ 里），不清 name 会继续按旧文件名登记 interval_timer，不清 kp/des 会让
    Sift Flann 方法沿用旧模板的特征点（当前映射的资产都是 Template matching，属潜伏
    问题，但这是个通用清理器）。
    """
    obj._image = None
    obj._kp = None
    obj._des = None
    del_cached_property(obj, 'name')
    del_cached_property(obj, 'kp')
    del_cached_property(obj, 'des')


# 庭院皮肤涉及的资产 key。直接从 model 的第一项推导，避免「映射表」与「默认套要做哪些
# key 的快照」两处各维护一份清单而脱节。
_MAIN_TARGETS = tuple(next(iter(main_costume_model.values())).keys())

# 出厂资产快照：{ (资产类, 属性名): {'file','roi_front','roi_back','threshold'} }
#
# 为什么需要：replace_img 就地改写类属性对象，默认套一旦被换成别的皮肤，它的出厂图就
# 再没有引用，探测时永远匹配不到「默认庭院」，回切也没东西可换回去。所以在首次真替换
# 之前冻结一份。此时 RuleImage 还没被任何地方加载过图，roi_front 就是 assets.py 里的
# 出厂值。
_default_main_snapshot: dict | None = None

# 当前已套用的皮肤，None 表示出厂状态。用于幂等（每个任务 __init__ 都会调
# check_costume）与默认套回切。
_applied_main: MainType | None = None


def _snapshot(obj: RuleImage) -> dict:
    """记下 RuleImage 的出厂取值，供回切默认套时重建。

    method 必须一起记：不记就只能在 _rebuild 里硬编码，回切默认套会把 method 改掉
    （当前涉及的资产都是 Template matching，属潜伏问题）。
    """
    return {
        'file': obj.file,
        'roi_front': tuple(obj.roi_front),
        'roi_back': obj.roi_back,
        'threshold': obj.threshold,
        'method': obj.method,
    }


def _rebuild(snapshot: dict) -> RuleImage:
    """按快照重建一个 RuleImage。

    不能直接复用快照里的原对象——那个对象已经被 replace_img 就地改写成别的皮肤了。
    """
    return RuleImage(roi_front=snapshot['roi_front'], roi_back=snapshot['roi_back'],
                     method=snapshot['method'], threshold=snapshot['threshold'],
                     file=snapshot['file'])


def _ensure_default_main_snapshot() -> None:
    """冻结默认庭院的出厂资产值。必须在首次真替换之前调用，且只做一次。"""
    global _default_main_snapshot
    if _default_main_snapshot is not None:
        return
    snap: dict = {}
    for target in _MAIN_TARGETS:
        asset_cls, attr = target
        # 目标类没有该属性时静默跳过，与 replace_img 的处理保持一致
        # （正常情况下 5 个 key 都齐：4 个在 GameUiAssets，I_PET_HOUSE 在 PetsAssets）
        if not hasattr(asset_cls, attr):
            continue
        snap[target] = _snapshot(getattr(asset_cls, attr))
    _default_main_snapshot = snap


# 卷轴（主题）皮肤。默认套的资产在 RestartAssets，靠出厂快照回切，不登记映射。
# 键是 (资产类, 属性名)：卷轴的收起/展开态挂在 RestartAssets 上，而 Plotline /
# ExperienceYoukai 的任务 MRO 里没有 RestartAssets，用 getattr(self, ...) 会静默跳过。
theme_costume_model = {
    ThemeType.COSTUME_THEME_1: {
        (RestartAssets, 'I_LOGIN_SCROOLL_CLOSE'): 'I_THEME_1_SCROLL_CLOSE',
        (RestartAssets, 'I_LOGIN_SCROOLL_OPEN'): 'I_THEME_1_SCROLL_OPEN',
    },
}

# 卷轴皮肤涉及的资产 key（收起态 + 展开态），默认套也要，回切时得知道还原哪几个。
_THEME_TARGETS = (
    (RestartAssets, 'I_LOGIN_SCROOLL_CLOSE'),
    (RestartAssets, 'I_LOGIN_SCROOLL_OPEN'),
)

_default_theme_snapshot: dict | None = None
_applied_theme: ThemeType | None = None


def _ensure_default_theme_snapshot() -> None:
    """冻结默认卷轴的出厂资产值。必须在首次真替换之前调用，且只做一次。"""
    global _default_theme_snapshot
    if _default_theme_snapshot is not None:
        return
    snap: dict = {}
    for target in _THEME_TARGETS:
        asset_cls, attr = target
        if not hasattr(asset_cls, attr):
            continue
        snap[target] = _snapshot(getattr(asset_cls, attr))
    _default_theme_snapshot = snap


# 探测候选表：(皮肤类型, 用于判定的资产列表)。惰性构建一次并缓存。
#
# 缓存的理由不是「CostumeAssets() 很贵」——那 70 个 RuleImage 是类属性、import 期建
# 一次，new 一个空实例很廉价且不丢 _image 缓存（实测 CostumeAssets().I_CHECK_MAIN_5
# is CostumeAssets().I_CHECK_MAIN_5 为 True）。真正的理由是默认套的候选必须靠
# _rebuild(快照) 新建对象（活对象已被就地改写），不缓存就每轮探测都要重新 imdecode
# 一次默认模板；顺带也省掉每轮对映射表的 hasattr/getattr 扫描。
_probe_table_main: dict | None = None
_probe_table_theme: dict | None = None

# 探测节流。Timer 未 start 时 _current == 0，reached() 恒为 True，所以新建即「已到点」，
# 首次探测不被拦；零命中后 reset() 才真正开始计时。
_probe_timer = Timer(2)

# 探针锁：探测成功即锁定，避免页面识别每轮都全量轮询。
# 解锁在 _app_handle_login() 开头（重试循环内，见 Task 6）。
_main_probe_locked = False
_theme_probe_locked = False

# 零命中告警去重：探测受 2s 节流，登录卡住时会反复零命中，不加限制会刷屏。
_probe_warned = False

# 庭院探测只用一个 key。RuleImage.match() 命中时会写 roi_front，而 I_MAIN_GOTO_* /
# I_PET_HOUSE 是要被点击的，被探测写脏会点错位置；I_CHECK_MAIN 只用于状态判定
# （page_main.check_button 与十余处 appear 检查），从不被点击。
_MAIN_PROBE_KEY = (GameUiAssets, 'I_CHECK_MAIN')


def _ensure_probe_tables() -> None:
    """惰性构建庭院/卷轴探测候选表，只做一次。"""
    global _probe_table_main, _probe_table_theme
    if _probe_table_main is not None:
        return
    _ensure_default_main_snapshot()
    _ensure_default_theme_snapshot()
    costume_assets = CostumeAssets()
    # 卷轴图放独立组件 tasks/Component/CostumeTheme/（与 CostumeBattle / CostumeShikigami
    # / CostumeCarpBanner 同构），不混进庭院那套 CostumeAssets
    theme_assets = CostumeThemeAssets()

    main_table: dict = {}
    # 默认套：活对象已被就地改写，只能从出厂快照重建；建一次后缓存住，别每次探测都重读盘
    if _MAIN_PROBE_KEY in _default_main_snapshot:
        main_table[MainType.COSTUME_MAIN] = [_rebuild(_default_main_snapshot[_MAIN_PROBE_KEY])]
    for main_type, model in main_costume_model.items():
        value = model.get(_MAIN_PROBE_KEY)
        # 皮肤资产还没采集的套直接不进表，探测不会为它花时间
        if value and hasattr(costume_assets, value):
            main_table[main_type] = [getattr(costume_assets, value)]

    theme_table: dict = {}
    # 卷轴一套两张（收起态 + 展开态），任一命中即算该套命中
    default_theme = [_rebuild(_default_theme_snapshot[t])
                     for t in _THEME_TARGETS if t in _default_theme_snapshot]
    if default_theme:
        theme_table[ThemeType.COSTUME_THEME_DEFAULT] = default_theme
    for theme_type, model in theme_costume_model.items():
        assets = [getattr(theme_assets, v) for v in model.values() if hasattr(theme_assets, v)]
        if assets:
            theme_table[theme_type] = assets

    _probe_table_main = main_table
    _probe_table_theme = theme_table


def _ordered_candidates(table: dict, configured):
    """把配置的那一套提到最前，其余保持插入顺序。

    先试配置的原因：配置对的时候一次匹配就结束，探测零额外开销；配置错时只多花一次匹配。
    顺序固定是为了消歧义——「命中就回写」不设门槛，多个候选同时命中时取第一个。
    """
    if configured in table:
        yield configured, table[configured]
    for key, assets in table.items():
        if key != configured:
            yield key, assets


def _warn_probe_missed_once(what: str) -> None:
    """零命中告警，每次解锁周期只打一条。"""
    global _probe_warned
    if _probe_warned:
        return
    _probe_warned = True
    logger.warning(f'Costume {what} probe found no match; '
                   f'the current skin may not be collected yet')


def reset_costume_module_state() -> None:
    """把模块级状态复位到出厂。仅供测试隔离使用。

    必须有这个函数：模块级快照会跨用例泄漏。fixture 把 _default_main_snapshot 复位为
    None，之后 _ensure_default_main_snapshot() 就会拿**被上一个用例改过的**资产值去冻结
    「出厂快照」，后续所有断言都建立在一个假的出厂值上。

    tests/device/ 的庭院用例调的是私有 _app_handle_login()，不经过解锁入口，
    也必须靠 autouse fixture 调这个函数，否则探针锁会跨用例污染。
    """
    global _default_main_snapshot, _default_theme_snapshot
    global _applied_main, _applied_theme
    global _probe_table_main, _probe_table_theme
    global _main_probe_locked, _theme_probe_locked, _probe_warned
    _default_main_snapshot = None
    _default_theme_snapshot = None
    _applied_main = None
    _applied_theme = None
    _probe_table_main = None
    _probe_table_theme = None
    _main_probe_locked = False
    _theme_probe_locked = False
    _probe_warned = False
    _probe_timer.clear()


def release_costume_probe_locks() -> None:
    """解锁探测。在 _app_handle_login() 开头调用。

    为什么是私有的 _app_handle_login 而不是公开的 app_handle_login：后者的
    `for _ in range(attempts)` 重试循环在函数体内（login.py:288-293），放循环外面
    只会解锁一次，第二轮重试就带着上一轮的锁状态进来。

    这次登录可能落到另一个号上，之前锁定的皮肤对它不一定成立；解锁后同一次登录里的
    探测会重新锁定。连节流一起 clear：切号后应该立刻能探，不该白等一个节流窗口。
    """
    global _main_probe_locked, _theme_probe_locked, _probe_warned
    _main_probe_locked = False
    _theme_probe_locked = False
    _probe_warned = False
    _probe_timer.clear()


class CostumeBase:
    def check_costume(self, config: CostumeConfig=None):
        if config is None:
            config: CostumeConfig = self.config.model.global_game.costume_config
        self.check_costume_main(config.costume_main_type)
        self.check_costume_theme(config.costume_theme_type)
        self.check_costume_carpbanner(config.costume_carpbanner_type)
        self.check_costume_battle(config.costume_battle_type)
        self.check_costume_shikigami(config.costume_shikigami_type)

    def replace_img(self,
                    target,
                    asset_after: RuleImage,
                    rp_roi_back: bool = True):
        """把 target 指向的资产对象就地改写为 asset_after。

        就地改写意味着影响是全进程的——这正是本机制生效的前提：所有任务读到的都是
        同一个对象，改一次处处生效。

        target 支持两种形态：
        - ``(资产类, 属性名)``：直接落到类属性上，不依赖调用方任务的 MRO。本次新迁的
          main / theme 两条链用这种。目标类没有该属性时静默跳过（跨类的正常情况）。
        - ``str``：旧的 ``getattr(self, 属性名)`` 路径，行为逐字节保持现状。
          carpbanner / battle / shikigami 三条链仍用它——它们的调用方任务都通过 MRO
          持有对应资产，替换本来就在正常工作，没有迁移的必要（那 30 多个 key 横跨
          GeneralBattleAssets / SwitchSoulAssets / 各 Costume*Assets 共 5 个类）。

        roi_front 走拷贝而不是共享引用：RuleImage.match() 命中时会写 roi_front[0]/[1]，
        共享引用会让一处匹配串改另一处资产。
        """
        if isinstance(target, tuple):
            asset_cls, attr = target
            if not hasattr(asset_cls, attr):
                return
            obj: RuleImage = getattr(asset_cls, attr)
        else:
            if not hasattr(self, target):
                return
            obj: RuleImage = getattr(self, target)
        obj.roi_front = list(asset_after.roi_front)
        if rp_roi_back:
            obj.roi_back = asset_after.roi_back
        obj.threshold = asset_after.threshold
        obj.file = asset_after.file
        _clear_rule_image_cache(obj)

    def check_costume_main(self, main_type: MainType):
        """套用指定庭院皮肤。幂等：同一套重复调用直接返回。

        默认套不是「什么都不做」而是「按出厂快照还原」——运行时探测会出现
        main5 → 默认 的回切，早退式实现会让默认套永远回不去。
        """
        global _applied_main
        _ensure_default_main_snapshot()
        if main_type == _applied_main:
            return
        logger.info(f'Switch main costume to {main_type}')
        targets: dict = {}
        if main_type == MainType.COSTUME_MAIN:
            # 默认套的资产对象已被就地改写，出厂值只能从快照重建
            targets = {t: _rebuild(s) for t, s in _default_main_snapshot.items()}
        else:
            costume_assets = CostumeAssets()
            for target, value in main_costume_model[main_type].items():
                if not hasattr(costume_assets, value):
                    # 皮肤资产还没采集（例如只登记了枚举项），跳过而不是抛异常
                    logger.warning(f'Main costume asset {value} not found, skip')
                    continue
                targets[target] = getattr(costume_assets, value)
        for target, asset in targets.items():
            self.replace_img(target, asset)
        if not targets:
            # 一个都没换成（资产未采集）：不能记成已套用，否则幂等守卫让它永不重试
            logger.warning(f'Main costume {main_type} applied nothing, keep previous state')
            return
        _applied_main = main_type

    def check_costume_theme(self, theme_type: ThemeType):
        """套用指定卷轴（主题）皮肤。幂等；默认套按出厂快照还原。

        卷轴图在独立组件 CostumeThemeAssets 上（与 CostumeBattle / CostumeShikigami 同构）。
        新皮肤的图还没采集时（映射里的资产在该类上不存在）打警告后跳过，
        与 check_costume_carpbanner / check_costume_shikigami 的既有处理一致。
        """
        global _applied_theme
        _ensure_default_theme_snapshot()
        if theme_type == _applied_theme:
            return
        logger.info(f'Switch theme costume to {theme_type}')
        targets: dict = {}
        if theme_type == ThemeType.COSTUME_THEME_DEFAULT:
            targets = {t: _rebuild(s) for t, s in _default_theme_snapshot.items()}
        else:
            costume_assets = CostumeThemeAssets()
            for target, value in theme_costume_model.get(theme_type, {}).items():
                if not hasattr(costume_assets, value):
                    logger.warning(f'Theme costume asset {value} not found, skip')
                    continue
                targets[target] = getattr(costume_assets, value)
        for target, asset in targets.items():
            self.replace_img(target, asset)
        if not targets:
            # 一个都没换成（本批 COSTUME_THEME_1 就是这种情况：图还没采集）：
            # 不能记成已套用，否则幂等守卫让它永不重试
            logger.warning(f'Theme costume {theme_type} applied nothing, keep previous state')
            return
        _applied_theme = theme_type

    def probe_costume_main(self) -> MainType | None:
        """在当前截图上轮询庭院候选，返回第一个命中的皮肤类型，都不中返回 None。

        只读 self.device.image，不重新截图——调用方刚取的那一帧就是判定依据。
        """
        _ensure_probe_tables()
        configured = self.config.model.global_game.costume_config.costume_main_type
        for main_type, assets in _ordered_candidates(_probe_table_main, configured):
            for asset in assets:
                if self.appear(asset):
                    return main_type
        return None

    def probe_costume_theme(self) -> ThemeType | None:
        """在当前截图上轮询卷轴候选。一套两张（收起态 / 展开态），任一张命中即算命中。"""
        _ensure_probe_tables()
        configured = self.config.model.global_game.costume_config.costume_theme_type
        for theme_type, assets in _ordered_candidates(_probe_table_theme, configured):
            for asset in assets:
                if self.appear(asset):
                    return theme_type
        return None

    def _apply_detected_costume_main(self, detected: MainType) -> None:
        """套用探测到的庭院皮肤并回写配置（配置自我修正）。

        只是一层薄包装：资产替换仍走 check_costume_main（幂等、支持回切默认套），
        这里只多做「回写配置 + 打日志」。

        命中就回写是既定语义：配置只是快路径，写错了下一次探测会纠正。回写必须留日志，
        那是这个「不设可信度门槛」的选择唯一的护栏。
        """
        config = self.config.model.global_game.costume_config
        old = config.costume_main_type
        self.check_costume_main(detected)
        if old == detected:
            logger.info(f'Costume main detected {detected} (matches config)')
            return
        config.costume_main_type = detected
        self.config.save()
        logger.info(f'Costume main detected {old} -> {detected}, config updated')

    def _apply_detected_costume_theme(self, detected: ThemeType) -> None:
        """套用探测到的卷轴皮肤并回写配置。语义同 _apply_detected_costume_main。"""
        config = self.config.model.global_game.costume_config
        old = config.costume_theme_type
        self.check_costume_theme(detected)
        if old == detected:
            logger.info(f'Costume theme detected {detected} (matches config)')
            return
        config.costume_theme_type = detected
        self.config.save()
        logger.info(f'Costume theme detected {old} -> {detected}, config updated')

    def try_detect_costume_main(self) -> MainType | None:
        """带节流与锁的庭院探测入口。命中即套用 + 回写并锁定。

        零命中打一条 warning（当前皮肤可能尚未采集），每次解锁周期只打一条——
        探测受 2s 节流，登录卡住时会反复零命中，不加限制会刷屏。
        """
        global _main_probe_locked, _probe_warned
        if _main_probe_locked or not _probe_timer.reached():
            return None
        _probe_timer.reset()
        detected = self.probe_costume_main()
        if detected is None:
            _warn_probe_missed_once('main')
            return None
        _main_probe_locked = True
        _probe_warned = False
        self._apply_detected_costume_main(detected)
        return detected

    def try_detect_costume(self) -> bool:
        """庭院与卷轴各探一次，共用一次节流窗口。**返回「是否刚修好了资产」，不是「是否在庭院」。**

        不能简单串两个 try_detect_costume_*：它们各自会 reset 节流，先跑的那个命中后
        会把后跑的那个挡在窗口外。这里只判一次节流，两个都探到。

        调用方（login）拿到 True 的正确用法是 `continue` 重新截图判定，让修正后的
        I_CHECK_MAIN 在下一帧被 courtyard_mark 正常匹配到 —— **不要把返回值直接当成
        「在庭院」的证据**：探针受 2s 节流 + 成功即锁，只在命中那一帧为真，而
        courtyard_timer 要求连续 2.5s 为真（login.py:217-219 任何一帧为假就 clear），
        拿单帧真去撑连续确认会把计时反复清掉，正好复现它要修的死循环。
        """
        global _main_probe_locked, _theme_probe_locked, _probe_warned
        if not _probe_timer.reached():
            return False
        _probe_timer.reset()
        hit = False
        if not _main_probe_locked:
            detected = self.probe_costume_main()
            if detected is not None:
                _main_probe_locked = True
                self._apply_detected_costume_main(detected)
                hit = True
        if not _theme_probe_locked:
            detected = self.probe_costume_theme()
            if detected is not None:
                _theme_probe_locked = True
                self._apply_detected_costume_theme(detected)
                hit = True
        if not hit:
            _warn_probe_missed_once('main/theme')
            return False
        _probe_warned = False
        return True

    def check_costume_carpbanner(self, carpbanner_type: CarpBannerType):
        if carpbanner_type == CarpBannerType.COSTUME_CARPBANNER_DEFAULT:
            return
        logger.info(f'Switch carp banner theme {carpbanner_type} (override realm assets)')
        carpbanner_assets = CostumeCarpBannerAssets()
        model = carpbanner_costume_model.get(carpbanner_type, {})
        for key, value in model.items():
            if not hasattr(carpbanner_assets, value):
                logger.warning(f'Carp banner asset {value} not found, skip')
                continue
            assert_value: RuleImage = getattr(carpbanner_assets, value)
            # 执行替换（覆盖结界皮肤的同名key）
            self.replace_img(key, assert_value)

    def check_costume_battle(self, battle_type: BattleType):
        if battle_type == BattleType.COSTUME_BATTLE_DEFAULT:
            return
        logger.info(f'Switch battle theme {battle_type}')
        costume_battle_assets = CostumeBattleAssets()
        for key, value in battle_theme_model[battle_type].items():
            assert_value: RuleImage = getattr(costume_battle_assets, value)
            # 绿标的坐标点范围不变
            if key == 'I_LOCAL':
                self.replace_img(key, assert_value, rp_roi_back=False)
            else:
                self.replace_img(key, assert_value)

    def check_costume_shikigami(self, shikigami_type: ShikigamiType):
        if shikigami_type == ShikigamiType.COSTUME_SHIKIGAMI_DEFAULT:
            return
        logger.info(f'Switch shikigami theme {shikigami_type}')
        shikigami_assets = CostumeShikigamiAssets()
        model = shikigami_costume_model.get(shikigami_type, {})
        for key, value in model.items():
            if not hasattr(shikigami_assets, value):
                # 尚未采集完成的资产，跳过
                continue
            assert_value: RuleImage = getattr(shikigami_assets, value)
            # 一般不需要固定 back ROI，如确有需要可在此为特例设置 rp_roi_back=False
            self.replace_img(key, assert_value)


if __name__ == '__main__':
    c = CostumeBase()
    c.check_costume_main(MainType.COSTUME_MAIN_2)
