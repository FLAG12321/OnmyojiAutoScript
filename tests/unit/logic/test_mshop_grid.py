# This Python file uses the following encoding: utf-8
"""神秘商店格位网格测试：命中中心→格位号的映射、币种数值判定、资产工厂。

这一层是纯算术，边界落点归哪一格最容易出 off-by-one，所以在这里穷举，
而不是等真机上点错格位才发现。
"""
from pathlib import Path

import pytest

from tasks.DailyAltAcc.config import CoinType, GoodsType
from tasks.DailyAltAcc.mshop_grid import (
    GOODS_TEMPLATES,
    GRID_X_EDGES,
    GRID_Y_EDGES,
    SHELF_ROI,
    coin_of,
    goods_rule,
    locate_slot,
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
def test_goods_template_files_exist():
    """模板路径写错只会在真机上崩在 RuleImage.load_image 里，这里提前抓。"""
    for goods, file in GOODS_TEMPLATES.items():
        assert (REPO_ROOT / file).is_file(), f'{goods.name} 模板缺失: {file}'


@pytest.mark.unit
def test_goods_rule_is_cached_and_searches_whole_shelf():
    """同一模板复用实例（省重复读盘），搜索区是整个货架，不同模板不共享实例。"""
    first = goods_rule(GOODS_TEMPLATES[GoodsType.shepi])
    second = goods_rule(GOODS_TEMPLATES[GoodsType.shepi])
    assert first is second
    assert first.roi_back == SHELF_ROI
    assert goods_rule(GOODS_TEMPLATES[GoodsType.fmpi]) is not first


@pytest.mark.unit
def test_goods_rule_cache_key_includes_threshold():
    """阈值是缓存键的一部分，否则换阈值会静默拿到旧实例。"""
    default = goods_rule(GOODS_TEMPLATES[GoodsType.heisui])
    stricter = goods_rule(GOODS_TEMPLATES[GoodsType.heisui], threshold=0.9)
    assert default is not stricter
    assert stricter.threshold == 0.9
