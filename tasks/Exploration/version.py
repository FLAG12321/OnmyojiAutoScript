# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import cv2
import numpy as np

from cached_property import cached_property

from module.atom.image import RuleImage
from module.logger import logger

from tasks.base_task import BaseTask
from tasks.Exploration.assets import ExplorationAssets


class Version(BaseTask):
    pass


class HighlightBattleButton(RuleImage):
    """先定位挑战图标，再用排除图标后的按钮外围平均亮度判断高亮。"""

    # log2 六帧中，高亮灰度均值为 98.04～130.93，非高亮为 50.88～60.24。
    # 取间隔中部的 80（0～255）；颜色均值仅用于诊断，不另加颜色限制。
    MIN_RING_BRIGHTNESS = 80.0
    # 普通图标 (444,287,42,39) 对应完整按钮 (422,262,93,90)。
    # 首领沿用相同外扩边距，核心排除范围按首领模板自身尺寸计算。
    RING_PADDING = (22, 25, 29, 26)

    def __init__(self, target: RuleImage):
        """独立保存匹配位置，避免改写普通探索共用的挑战按钮资源。"""
        super().__init__(roi_front=target.roi_front, roi_back=target.roi_back,
                         method=target.method, threshold=target.threshold, file=target.file)

    def ring_mean(self, image, roi):
        """返回外围 RGB 均值与灰度均值；按钮被屏幕裁切时等待后续完整帧。"""
        x, y, w, h = map(int, roi)
        left, top, right, bottom = self.RING_PADDING
        x1, y1 = x - left, y - top
        x2, y2 = x + w + right, y + h + bottom
        if x1 < 0 or y1 < 0 or x2 > image.shape[1] or y2 > image.shape[0]:
            return None
        # 只统计外框内、模板矩形外的像素，排除刀剑本身的颜色和亮度。
        crop = image[y1:y2, x1:x2]
        mask = np.ones(crop.shape[:2], dtype=np.uint8)
        mask[top:top + h, left:left + w] = 0
        mean_color = cv2.mean(crop, mask=mask)[:3]
        brightness = cv2.mean(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), mask=mask)[0]
        return mean_color, brightness

    def match(self, image, threshold: float = None) -> bool:
        """遍历去重后的全部图标，找怪与点击前复查均执行外围亮度筛选。"""
        # 普通探索会收窄共享按钮的搜索范围，悬赏每帧独立搜索完整截图。
        search_roi = (0, 0, image.shape[1], image.shape[0])
        for _, x, y, w, h in self.match_all_any(image, threshold=threshold, roi=search_roi):
            roi = [int(x), int(y), int(w), int(h)]
            result = self.ring_mean(image, roi)
            if result is None:
                continue
            mean_color, brightness = result
            logger.debug(f'{self.name} roi={roi}, ring RGB='
                         f'{tuple(round(value, 2) for value in mean_color)}, '
                         f'brightness={brightness:.2f}, threshold={self.MIN_RING_BRIGHTNESS}')
            if brightness >= self.MIN_RING_BRIGHTNESS:
                self.roi_front = roi
                return True
        return False


class HighLight(BaseTask, ExplorationAssets):

    @cached_property
    def I_HIGHLIGHT_NORMAL_BATTLE_BUTTON(self) -> HighlightBattleButton:
        """普通怪仅在挑战按钮外围高亮时匹配。"""
        return HighlightBattleButton(self.I_NORMAL_BATTLE_BUTTON)

    @cached_property
    def I_HIGHLIGHT_BOSS_BATTLE_BUTTON(self) -> HighlightBattleButton:
        """首领与普通怪共用外围判定，避免无条件挑战非悬赏首领。"""
        return HighlightBattleButton(self.I_BOSS_BATTLE_BUTTON)


if __name__ == '__main__':
    # 手动调试时同时检查普通怪与首领的外围亮度。
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = HighLight(c, d)
    # t.screenshot()
    #
    for i in range(10):
        t.screenshot()
        logger.info(t.appear(t.I_HIGHLIGHT_NORMAL_BATTLE_BUTTON))
        logger.info(t.appear(t.I_HIGHLIGHT_BOSS_BATTLE_BUTTON))
