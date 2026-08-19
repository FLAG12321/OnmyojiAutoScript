# This Python file uses the following encoding: utf-8
"""神秘商店格位网格测试：命中中心→格位号的映射、币种数值判定、资产工厂。

这一层是纯算术，边界落点归哪一格最容易出 off-by-one，所以在这里穷举，
而不是等真机上点错格位才发现。
"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from module.ocr.base_ocr import OcrMode
from tasks.DailyAltAcc.config import CoinType, DailyAltAccConfig, GoodsType
from tasks.DailyAltAcc.mshop_grid import (
    BLACK_DARUMA_MIN_PRICE,
    FLOWER_UNLOCK,
    GOODS_ASSETS,
    GOODS_NAMES,
    GRID_X_EDGES,
    GRID_Y_EDGES,
    SHARD_MAX_PRICE,
    SHELF_ROI,
    coin_of,
    enabled_goods,
    is_unlocked,
    locate_slot,
    price_roi,
    price_rule,
    refine_goods,
)

# 仓库根目录：本文件在 tests/unit/logic/ 下，往上三层
REPO_ROOT = Path(__file__).resolve().parents[3]

# 8 个价格 OCR ROI 的中心点，由 tasks/DailyAltAcc/assets.py 的 O_MS_PRICENUM_1..8
# 按 (x + w // 2, y + h // 2) 算得。用已验证的真实资产坐标当输入，
# 避免测试自造一套坐标却和线上资产脱节。
PRICE_CENTERS = [
    (248, 275), (474, 274), (696, 276), (924, 274),
    (247, 536), (473, 537), (697, 536), (922, 536),
]


@pytest.mark.unit
def test_locate_slot_maps_price_centers_to_1_through_8():
    """8 个格位的价格中心必须映射到 1..8，顺序行优先（上排 1-4、下排 5-8）。"""
    for index, (cx, cy) in enumerate(PRICE_CENTERS):
        assert locate_slot(cx, cy) == index + 1, f'({cx},{cy}) 应归格位 {index + 1}'


@pytest.mark.unit
def test_locate_slot_column_boundary_is_left_closed():
    """列边界左闭右开：差一个像素就换格，必须逐个边界钉死。"""
    assert locate_slot(360, 275) == 1
    assert locate_slot(361, 275) == 2
    assert locate_slot(585, 275) == 2
    assert locate_slot(586, 275) == 3
    assert locate_slot(809, 275) == 3
    assert locate_slot(810, 275) == 4


@pytest.mark.unit
def test_locate_slot_row_boundary_is_left_closed():
    """行边界左闭右开：329 归上排格位 1，330 归下排格位 5。"""
    assert locate_slot(248, 329) == 1
    assert locate_slot(248, 330) == 5


@pytest.mark.unit
def test_locate_slot_returns_none_outside_grid():
    """落在网格外必须返回 None，不能钳到最近的边缘格 —— 那会点错格位。"""
    assert locate_slot(134, 275) is None
    assert locate_slot(1036, 275) is None
    assert locate_slot(248, 106) is None
    assert locate_slot(248, 561) is None


@pytest.mark.unit
def test_grid_contains_shelf_roi():
    """网格必须完全包含模板搜索区，否则会出现「命中落在格位外」。

    这是回归护栏：以后有人改 SHELF_ROI 或任一 edge，先炸这条测试，
    而不是等真机上出现无法归格的命中。
    """
    x, y, w, h = SHELF_ROI
    corners = [(x, y), (x + w - 1, y), (x, y + h - 1), (x + w - 1, y + h - 1)]
    for cx, cy in corners:
        assert locate_slot(cx, cy) is not None, f'搜索区角点 ({cx},{cy}) 落在网格外'
    assert GRID_X_EDGES[0] <= x
    assert x + w - 1 < GRID_X_EDGES[-1]
    assert GRID_Y_EDGES[0] <= y
    assert y + h - 1 < GRID_Y_EDGES[-1]


@pytest.mark.unit
def test_coin_of_uses_10000_as_threshold():
    """>10000 金币、<=10000 勾玉；<=0 是 OCR 失败，不能当勾玉放过去。"""
    assert coin_of(10001) == CoinType.gold
    assert coin_of(50000) == CoinType.gold
    assert coin_of(10000) == CoinType.jade
    assert coin_of(9999) == CoinType.jade
    assert coin_of(1) == CoinType.jade
    assert coin_of(0) == CoinType.unknow
    assert coin_of(-1) == CoinType.unknow


@pytest.mark.unit
def test_goods_assets_exist_on_mshop_and_search_whole_shelf():
    """5 个货物资产必须已由 image1.json 生成并挂到 Mshop 上，搜索区覆盖整个货架。

    资产变量名写错、或 image1.json 漏注册，都会让运行期 getattr 抛
    AttributeError；这里提前抓，不用等真机。

    搜索区不断言精确等于 SHELF_ROI —— 资源编辑器重新框选时会有几像素出入。
    真正要守的是「搜索区四角都能归到格位」，即命中不可能落在网格外。
    """
    from tasks.DailyAltAcc.mshop import Mshop
    for goods, name in GOODS_ASSETS.items():
        rule = getattr(Mshop, name, None)
        assert rule is not None, f'{goods.name} 的资产 {name} 不存在'
        assert Path(rule.file).is_file(), f'{name} 模板文件缺失: {rule.file}'
        x, y, w, h = rule.roi_back
        for cx, cy in ((x, y), (x + w - 1, y), (x, y + h - 1), (x + w - 1, y + h - 1)):
            assert locate_slot(cx, cy) is not None, \
                f'{name} 搜索区角点 ({cx},{cy}) 落在格位网格外'


@pytest.mark.unit
def test_every_goods_type_has_asset_and_name():
    """每个 GoodsType 都必须配齐资产名与中文名，加货物漏配一处就在这里炸。

    中文名同时是 [STAT] 日志 goods 字段的值，缺失会让统计面板出现空货名。
    """
    for goods in GoodsType:
        assert goods in GOODS_ASSETS, f'{goods.name} 缺资产变量名'
        assert goods in GOODS_NAMES, f'{goods.name} 缺中文名'
        assert GOODS_NAMES[goods], f'{goods.name} 中文名为空'


@pytest.mark.unit
def test_price_rule_is_cached_and_matches_lattice():
    """价格 RuleOcr 按格位缓存复用，roi 严格落在点阵上，name 带格位号可辨。

    RuleOcr 只读不写自身 roi，所以共享缓存安全（与 RuleImage.match 会写回
    roi_front 不同）。
    """
    for slot in range(1, 9):
        rule = price_rule(slot)
        assert price_rule(slot) is rule, f'格位 {slot} 没走缓存'
        assert tuple(rule.roi) == price_roi(slot)
        assert str(slot) in rule.name, f'{rule.name} 分不出格位号'
    assert price_rule(1) is not price_rule(2)


@pytest.mark.unit
def test_price_rule_is_digit_mode():
    """必须是 Digit 模式，否则 _ocr_price 的 int() 会拿到非数字串炸掉。"""
    assert price_rule(1).mode == OcrMode.DIGIT


@pytest.mark.unit
def test_price_assets_removed_from_generated_assets():
    """8 个 O_MS_PRICENUM_* 已从 ocr.json 与 assets.py 删除，ROI 改由点阵现造。

    留着会有两处事实源：assets 里一套手绘 ROI、代码里一套点阵，改一处漏一处。
    """
    from tasks.DailyAltAcc.mshop import Mshop
    for slot in range(1, 9):
        assert not hasattr(Mshop, f'O_MS_PRICENUM_{slot}'), \
            f'O_MS_PRICENUM_{slot} 仍在 assets.py 中，与点阵重复'
    # 只禁真实取用，不禁注释里提到旧名（说明「不再走 O_MS_PRICENUM_*」是合理的）
    source = (REPO_ROOT / 'tasks/DailyAltAcc/mshop.py').read_text(encoding='utf-8')
    assert "f'O_MS_PRICENUM_{slot}'" not in source
    assert 'self.O_MS_PRICENUM' not in source


@pytest.mark.unit
def test_price_lattice_covers_authored_rois():
    """点阵化后的每个 ROI 必须完整覆盖原手绘框，只放大不裁切。

    原手绘框（改造前 ocr.json 的取值）已在 3 张真实截图上验证过 OCR 读数正确，
    所以「新框完整包含旧框」是读数不退化的充分条件。
    """
    authored = {
        1: (173, 253, 151, 44), 2: (398, 256, 152, 37),
        3: (619, 258, 155, 37), 4: (846, 255, 156, 38),
        5: (169, 515, 157, 43), 6: (400, 516, 147, 42),
        7: (620, 516, 155, 41), 8: (843, 516, 159, 41),
    }
    for slot, (ax, ay, aw, ah) in authored.items():
        lx, ly, lw, lh = price_roi(slot)
        assert lx <= ax and ly <= ay, f'格位 {slot} 点阵左上角切进了原框'
        assert lx + lw >= ax + aw, f'格位 {slot} 点阵右边界切掉了原框'
        assert ly + lh >= ay + ah, f'格位 {slot} 点阵下边界切掉了原框'


@pytest.mark.unit
def test_price_roi_centers_map_back_to_own_slot():
    """每个价格 ROI 的中心必须落回自己的格位，点阵与网格不能错位。"""
    for slot in range(1, 9):
        x, y, w, h = price_roi(slot)
        assert locate_slot(x + w // 2, y + h // 2) == slot


@pytest.mark.unit
def test_msfind_uses_grid_scan_not_per_slot_assets():
    """MsFind 必须走格位扫描，且彻底不再引用 24 个逐格位商品资产与 16 个币种资产。

    这些资产条目已从 res/*.json 删除，源码断言防止有人回退到旧路径。
    """
    source = (REPO_ROOT / 'tasks/DailyAltAcc/mshop.py').read_text(encoding='utf-8')
    assert '_scan_slots' in source
    assert '_should_buy' in source
    assert 'I_MS_GOODS_' not in source
    assert 'I_MS_PRICE_' not in source
    assert 'I_MS_PRICES_' not in source
    assert 'I_MS_ALL_' not in source
    # 只禁方法定义，不禁注释里提到旧名 —— 文档里说明「沿用原 InfoFilter 规则」是合理的
    assert 'def FindGoodsType' not in source
    assert 'def FindCoinTypeAndCoinNum' not in source
    assert 'def InfoFilter' not in source


@pytest.mark.unit
def test_goods_assets_are_matched_all_any_only():
    """货物资产是共享类属性，只能调 match_all_any；调 match 会写脏 roi_front。

    match() 把命中坐标写回 roi_front（module/atom/image.py:166），在跨任务、
    跨多开实例共享的类属性上就是污染。这条锁住调用方式。
    """
    source = (REPO_ROOT / 'tasks/DailyAltAcc/mshop.py').read_text(encoding='utf-8')
    assert 'match_all_any' in source
    # 资产经 getattr(self, GOODS_ASSETS[goods]) 取出后只允许 match_all_any
    assert 'rule.match(' not in source


@pytest.mark.unit
def test_msfind_notify_title_is_mystery_shop():
    """通知标题必须是神秘商店提醒，不能沿用协作任务的遗留标题。"""
    source = (REPO_ROOT / 'tasks/DailyAltAcc/mshop.py').read_text(encoding='utf-8')
    assert "title='神秘商店提醒'" in source
    assert '协作任务提醒' not in source


@pytest.mark.unit
def test_enabled_goods_by_flower_level():
    """花数逐级解锁：一花放神秘符咒，二花放御行达摩碎片，三花放御行达摩。

    零花只扫不限花数的两类，漏配会让低花账号白扫或高花账号漏货。
    """
    assert enabled_goods(0) == [GoodsType.orochi_scale, GoodsType.demon_soul]
    assert enabled_goods(1) == [GoodsType.orochi_scale, GoodsType.demon_soul,
                                GoodsType.mystery_amulet]
    assert enabled_goods(2) == [GoodsType.orochi_scale, GoodsType.demon_soul,
                                GoodsType.mystery_amulet, GoodsType.skill_shard]
    assert enabled_goods(3) == [GoodsType.orochi_scale, GoodsType.demon_soul,
                                GoodsType.mystery_amulet, GoodsType.skill_shard,
                                GoodsType.black_daruma]


@pytest.mark.unit
def test_refine_goods_splits_shard_and_black_daruma_by_price():
    """碎片(<=300)与黑蛋(>=960)图标相近，价格是唯一可靠判据，双向都要能纠正。"""
    # 模板认成碎片但价格是黑蛋区间 -> 纠成黑蛋
    assert refine_goods(GoodsType.skill_shard, 960) == GoodsType.black_daruma
    assert refine_goods(GoodsType.skill_shard, 1200) == GoodsType.black_daruma
    # 模板认成黑蛋但价格是碎片区间 -> 纠成碎片
    assert refine_goods(GoodsType.black_daruma, 300) == GoodsType.skill_shard
    assert refine_goods(GoodsType.black_daruma, 72) == GoodsType.skill_shard
    # 落在 300~960 的空隙里，两边都不像，保持模板原判不硬猜
    assert refine_goods(GoodsType.skill_shard, 500) == GoodsType.skill_shard
    assert refine_goods(GoodsType.black_daruma, 500) == GoodsType.black_daruma
    # 非御行达摩系一律原样返回
    assert refine_goods(GoodsType.orochi_scale, 82500) == GoodsType.orochi_scale
    assert refine_goods(GoodsType.mystery_amulet, 55) == GoodsType.mystery_amulet


@pytest.mark.unit
def test_is_unlocked_matches_flower_table():
    """解锁判定要和 FLOWER_UNLOCK 一致，否则会推送账号买不到的货。"""
    assert is_unlocked(GoodsType.orochi_scale, 0)
    assert not is_unlocked(GoodsType.mystery_amulet, 0)
    assert is_unlocked(GoodsType.mystery_amulet, 1)
    assert not is_unlocked(GoodsType.skill_shard, 1)
    assert is_unlocked(GoodsType.skill_shard, 2)
    assert not is_unlocked(GoodsType.black_daruma, 2)
    assert is_unlocked(GoodsType.black_daruma, 3)


@pytest.mark.unit
def test_flower_unlock_covers_every_goods_type():
    """每个 GoodsType 都要有解锁门槛，漏配会让 is_unlocked 抛 KeyError。"""
    for goods in GoodsType:
        assert goods in FLOWER_UNLOCK, f'{goods.name} 缺花数门槛'


@pytest.mark.unit
def test_shard_and_black_daruma_price_ranges_do_not_overlap():
    """碎片上限必须严格小于黑蛋下限，否则价格判据失效。"""
    assert SHARD_MAX_PRICE < BLACK_DARUMA_MIN_PRICE


@pytest.mark.unit
def test_orphan_assets_stay_removed():
    """已删除的孤儿资产不得复活，其模板文件也不该回到 mshop/ 目录。

    I_MS_GOLD / I_MS_JADE：币种改由价格阈值判定（coin_of），图标匹配已无用；
    I_MS_FLAG：改造前即 0 引用；
    I_MS_FMPI：指向的 ms_fmpi.png 磁盘与 git 全历史都不存在，条目本身是坏的。
    """
    from tasks.DailyAltAcc.mshop import Mshop
    for name in ('I_MS_GOLD', 'I_MS_JADE', 'I_MS_FLAG', 'I_MS_FMPI'):
        assert not hasattr(Mshop, name), f'{name} 已删除却又出现在 assets.py'
    mshop_dir = REPO_ROOT / 'tasks/DailyAltAcc/mshop'
    for png in ('mshop_ms_gold.png', 'mshop_ms_jade.png', 'mshop_ms_flag.png'):
        assert not (mshop_dir / png).exists(), f'{png} 已删除却又回到目录'


@pytest.mark.unit
def test_no_unused_png_in_mshop_dir():
    """mshop/ 下每个 PNG 都必须被 image1.json 引用，不留孤儿。

    这条是持续护栏：以后再删条目忘删图、或加图忘注册，都会在这里暴露。
    """
    import json
    mshop_dir = REPO_ROOT / 'tasks/DailyAltAcc/mshop'
    referenced = {e['imageName']
                  for e in json.loads((mshop_dir / 'image1.json').read_text(encoding='utf-8'))}
    on_disk = {p.name for p in mshop_dir.glob('*.png')}
    assert on_disk - referenced == set(), f'有 PNG 未被任何条目引用: {on_disk - referenced}'
    assert referenced - on_disk == set(), f'有条目指向不存在的 PNG: {referenced - on_disk}'


@pytest.mark.unit
def test_isflower_is_int_range_0_to_3():
    """isflower 是 0~3 的花数而非 bool；存量配置里的 false 会被 pydantic 收成 0。"""
    assert DailyAltAccConfig().isflower == 0
    for value in (0, 1, 2, 3):
        assert DailyAltAccConfig(isflower=value).isflower == value
    # 存量配置是 bool，必须能平滑迁移
    assert DailyAltAccConfig(isflower=False).isflower == 0
    with pytest.raises(ValidationError):
        DailyAltAccConfig(isflower=4)
    with pytest.raises(ValidationError):
        DailyAltAccConfig(isflower=-1)
