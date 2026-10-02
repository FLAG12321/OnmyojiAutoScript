# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from types import MethodType

import numpy as np

from module.atom.image import RuleImage
from module.device.humanize import get_current_humanizer



class RuleGif:
    # 大部分实现同RuleImage 的接口

    # 挂载与卸载共用清单，只恢复本组件实际覆盖过的属性。
    _ATTACH_METHODS = ('pre_process', 'search', 'search_with_multi_scale',
                       'match', 'match_multi_scale', 'coord', 'front_center')
    _ATTACH_STATE = ('targets', 'roi_front', 'roi_back', 'appear_target', 'name', '_match_init')

    @staticmethod
    def attach_to(image_obj: RuleImage, targets: list[RuleImage]) -> None:
        """原地挂载多帧匹配，保留页面导航持有的 RuleImage 对象引用。

        只在调用时生效，普通 match 仍为单尺度多帧匹配。
        挂载前保存宿主属性，静态皮肤回切时可用 detach_from 恢复。
        """
        # 空帧组无法匹配，宿主作为自身帧会递归调用 match；先检查再修改。
        if not targets or any(target is image_obj for target in targets):
            raise ValueError('多帧挂载需要非空帧组，且帧不能是宿主对象自身')
        # 连续换动画帧组仍保留首次挂载前的状态，不能把上一组动画当成静态原值。
        if '_gif_original_state' not in image_obj.__dict__:
            image_obj._gif_original_state = {
                name: image_obj.__dict__[name]
                for name in RuleGif._ATTACH_METHODS + RuleGif._ATTACH_STATE
                if name in image_obj.__dict__
            }
        image_obj.targets = list(targets)
        image_obj.roi_front = [0, 0, 0, 0]
        image_obj.roi_back = targets[0].roi_back
        image_obj.appear_target = targets[0]
        # 挂载对象固定使用首帧名称；独立 RuleGif 保留当前命中帧名称的旧行为。
        image_obj.name = targets[0].name
        image_obj._match_init = False
        # 显式绑定匹配及落点方法，不把其他新增辅助函数意外覆盖到宿主上。
        for name in RuleGif._ATTACH_METHODS:
            setattr(image_obj, name, MethodType(getattr(RuleGif, name), image_obj))

    @staticmethod
    def detach_from(image_obj: RuleImage) -> None:
        """原地恢复挂载前的方法与区域，移除动画状态；未挂载时不做处理。"""
        original = image_obj.__dict__.pop('_gif_original_state', None)
        if original is None:
            return
        for name in RuleGif._ATTACH_METHODS + RuleGif._ATTACH_STATE:
            image_obj.__dict__.pop(name, None)
        image_obj.__dict__.update(original)

    @property
    def name(self) -> str:
        return self.appear_target.name

    def __init__(self, targets: list[RuleImage]):
        self.targets = targets
        self.roi_front: list = [0, 0, 0, 0]
        # 与 RuleImage 的区域、等待状态接口兼容，不切换现有匹配方式。
        self.roi_back = targets[0].roi_back
        self._match_init = False
        self.appear_target = targets[0]

    def pre_process(self, image):
        return image


    def search(self, image, roi: list = None, threshold: float = None) -> tuple:
        """

        :param image:
        :param roi:
        :param threshold:
        :return: bool
        第一项是否为出现, 第二项为匹配的RuleImage
        """
        image = self.pre_process(image)
        #
        threshold = self.targets[0].threshold if threshold is None else threshold
        # 挂载宿主仍遵守 RuleImage 的搜索区域契约；独立 GIF 保留首帧区域语义。
        if roi is None:
            roi = self.roi_back if isinstance(self, RuleImage) else self.targets[0].roi_back
        source = self.targets[0].corp(image, roi)
        for target in self.targets:
            target.roi_back = roi
            height, width = target.image.shape[:2]
            # 宿主收窄区域后，较大的帧无法参与普通匹配。
            if height > source.shape[0] or width > source.shape[1]:
                continue
            if target.match(image, threshold):
                # 多尺度匹配会改帧的宽高；普通匹配应恢复到实际模板尺寸。
                target.roi_front[2:] = [width, height]
                self.roi_front = target.roi_front
                self.appear_target = target
                return True, target
        return False, None

    def search_with_multi_scale(self, image, roi=None, threshold=None,
                                scales=None, scale_range=None) -> tuple:
        """逐帧尝试缩放匹配，返回是否命中及命中的帧。

        未指定尺度时使用 0.8～1.1、步长 0.05；显式 scales 不被默认范围覆盖。
        """
        # 整张截图仅预处理一次，保留 HighlightGif 等派生类的预处理能力。
        image = self.pre_process(image)
        threshold = self.targets[0].threshold if threshold is None else threshold
        # 与普通搜索一致，挂载后优先采用宿主当前的区域。
        if roi is None:
            roi = self.roi_back if isinstance(self, RuleImage) else self.targets[0].roi_back
        if scales is None and scale_range is None:
            scale_range = (0.8, 1.1, 0.05)
        for target in self.targets:
            target.roi_back = roi
            if target.match_multi_scale(image, threshold=threshold,
                                        scales=scales, scale_range=scale_range):
                # 保存本次最佳匹配区域，避免后续帧匹配改写已记录的落点。
                self.roi_front = list(target.roi_front)
                self.appear_target = target
                return True, target
        return False, None

    def match_multi_scale(self, image, threshold: float = None,
                          scales: list = None, scale_range: tuple = None) -> bool:
        """对齐 RuleImage 的多尺度入口，供公共 appear_multi_scale 显式调用。"""
        return self.search_with_multi_scale(
            image, threshold=threshold, scales=scales, scale_range=scale_range)[0]

    def match(self, image, threshold: float = None) -> bool:
        return self.search(image, threshold=threshold)[0]


    def coord(self) -> tuple:
        # 维度 A 落点采样（Plan Task 13）：有绑定且启用时用拟人 RNG，无 context 或
        # sample_point 返回 None（off/回退）时走原 np.random.randint 均匀采样
        x, y, w, h = self.roi_front
        humanizer = get_current_humanizer()
        if humanizer is not None:
            point = humanizer.sample_point((x, y, w, h))
            if point is not None:
                return point
        return x + np.random.randint(0, w), y + np.random.randint(0, h)

    def front_center(self) -> tuple:
        x, y, w, h = self.roi_front
        return int(x + w//2), int(y + h//2)
