# This Python file uses the following encoding: utf-8
"""神秘商店货架的格位化定位（纯逻辑层）。

只做坐标算术与数据结构，不依赖 device / config，便于单测穷举边界。
设备交互与编排在 tasks/DailyAltAcc/mshop.py。
"""
from dataclasses import dataclass

from module.atom.image import RuleImage
from tasks.DailyAltAcc.config import CoinType, GoodsType

# 货架总区域，沿用 assets.py 里 I_MS_ALL_* 的 roi_back（已验证坐标）
SHELF_ROI = (139, 107, 887, 454)

# 8 格位网格：4 列 × 2 行的连续铺砖。
# 列边界由 8 个价格 OCR ROI 的中心（x = 248/474/697/923，列间距约 225px）取相邻中点；
# 行边界取两行「商品 + 价格」整段之间的中点。
# 不变量：网格 x∈[135,1036)、y∈[107,561) 完全包含 SHELF_ROI，
# 因此任何命中都不可能落在网格外 —— 越界只可能是代码 bug，由单测守护。
GRID_X_EDGES = (135, 361, 586, 810, 1036)
GRID_Y_EDGES = (107, 330, 561)
GRID_COLS = 4

# 币种阈值：神秘商店金币价 > 10000，勾玉价 <= 10000。
# 魂玉不会给这些货标价，所以纯数值判定是完备的，不需要再匹配币种图标。
COIN_THRESHOLD = 10000

# 货物清单：加货物只加一行，扫描逻辑不用改。
# 要扫哪几类由调用方按配置挑选后传入 _scan_slots，本模块不读配置。
GOODS_TEMPLATES = {
    GoodsType.shepi: './tasks/DailyAltAcc/mshop/mshop_ms_shepi.png',
    GoodsType.fmpi: './tasks/DailyAltAcc/mshop/mshop_ms_fmpi.png',
    GoodsType.heisui: './tasks/DailyAltAcc/mshop/mshop_ms_heisui.png',
}

# 播报用中文名，避免日志/通知里出现 GoodsType.shepi 这种字面量
GOODS_NAMES = {
    GoodsType.shepi: '蛇皮',
    GoodsType.fmpi: '逢魔皮',
    GoodsType.heisui: '黑碎',
}

COIN_NAMES = {
    CoinType.jade: '勾玉',
    CoinType.gold: '金币',
    CoinType.unknow: '未知币种',
}


@dataclass
class SlotItem:
    """货架上某个格位识别出的商品。"""

    slot: int          # 格位号 1..8，行优先
    goods: GoodsType
    price: int
    coin: CoinType
    score: float       # 模板匹配得分，同格冲突时取优


def _bucket(value: int, edges: tuple) -> int | None:
    """左闭右开分桶：edges[i] <= value < edges[i+1] 时返回 i，越界返回 None。"""
    for index in range(len(edges) - 1):
        if edges[index] <= value < edges[index + 1]:
            return index
    return None


def locate_slot(cx: int, cy: int) -> int | None:
    """把模板命中框的中心点映射到格位号。

    :param cx: 命中框中心 x
    :param cy: 命中框中心 y
    :return: 格位号 1..8；落在网格外返回 None（调用方应记 warning 并跳过）
    """
    col = _bucket(cx, GRID_X_EDGES)
    row = _bucket(cy, GRID_Y_EDGES)
    if col is None or row is None:
        return None
    return row * GRID_COLS + col + 1


def coin_of(price: int) -> CoinType:
    """按价格数值判定币种，price <= 0 视为 OCR 失败。"""
    if price <= 0:
        return CoinType.unknow
    if price > COIN_THRESHOLD:
        return CoinType.gold
    return CoinType.jade


# 按 (模板路径, 阈值) 缓存，避免同一模板重复读盘；
# 阈值进键是必须的，否则换阈值会静默拿到旧实例。
_RULE_CACHE: dict[tuple[str, float], RuleImage] = {}


def goods_rule(file: str, threshold: float = 0.8) -> RuleImage:
    """通用商品资产工厂：按模板路径造一个搜索整个货架的 RuleImage。

    用工厂而不是「改共享类属性的 .file」，原因有两条：
    1. RuleImage.name 是 cached_property（module/base/decorator.py:86），取值后会
       替换成写进 __dict__ 的普通属性；换 .file 必须同时清 _image 和
       __dict__['name']，否则日志名与模板图停在首次的值。
    2. 改共享类属性会在多开实例间互相串 —— 与 tasks/RichMan/mall/special.py:156
       就地修改 O_SP_RES_NUMBER.roi 是同一类问题。

    返回的实例只允许调 match_all_any()，禁止调 match()：match() 会把命中坐标写回
    roi_front（module/atom/image.py:166），在共享实例上就是跨调用污染；
    match_all_any() 不传 roi 参数时对实例零副作用（module/atom/image.py:278
    仅在传了 roi 时改 roi_back）。
    """
    key = (file, threshold)
    if key not in _RULE_CACHE:
        _RULE_CACHE[key] = RuleImage(roi_front=SHELF_ROI, roi_back=SHELF_ROI,
                                     threshold=threshold, method='Template matching',
                                     file=file)
    return _RULE_CACHE[key]
