import numpy as np
import pytest
from pathlib import Path


class TestTemplateMatching:
    """纯逻辑层面测试模板匹配的参数和行为——不依赖真实 Device"""

    def test_rule_image_name_extraction(self):
        """RuleImage 从文件名提取名字"""
        from module.atom.image import RuleImage
        img = RuleImage(
            roi_front=(0, 0, 100, 100),
            roi_back=(0, 0, 100, 100),
            method="Template matching",
            threshold=0.8,
            file="I_CHECK_MAIN.png",
        )
        assert img.name == "I_CHECK_MAIN"

    def test_rule_image_equality_by_name(self):
        from module.atom.image import RuleImage
        a = RuleImage((0, 0, 10, 10), (0, 0, 10, 10), "Template matching", 0.8, "A.png")
        b = RuleImage((5, 5, 15, 15), (5, 5, 15, 15), "Template matching", 0.9, "A.png")
        c = RuleImage((0, 0, 10, 10), (0, 0, 10, 10), "Template matching", 0.8, "B.png")
        assert a == b  # 同名即相等
        assert a != c

    def test_roi_front_stored_as_list(self):
        from module.atom.image import RuleImage
        img = RuleImage(
            roi_front=(10, 20, 30, 40),
            roi_back=(0, 0, 100, 100),
            method="Template matching",
            threshold=0.8,
            file="test.png",
        )
        assert img.roi_front == [10, 20, 30, 40]

    def test_match_result_on_perfect_template(self):
        """使用包含自身模板的大图，验证 OpenCV matchTemplate 能找到"""
        import cv2
        template = np.random.randint(0, 255, (20, 20, 3), dtype=np.uint8)
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        scene[50:70, 80:100] = template

        result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        assert max_val > 0.95
        assert max_loc == (80, 50)
