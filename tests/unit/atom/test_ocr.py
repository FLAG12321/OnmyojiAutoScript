import numpy as np
import pytest


class TestOcrPreProcessing:
    def test_rule_ocr_instantiation(self):
        """验证 RuleOcr 可以正常实例化"""
        from module.atom.ocr import RuleOcr
        from module.ocr.base_ocr import OcrMode

        ocr = RuleOcr(
            name="TestOcr",
            mode="FULL",
            method="DEFAULT",
            roi=(0, 0, 100, 30),
            area=(0, 0, 100, 30),
            keyword="",
        )
        assert ocr.mode == OcrMode.FULL
        assert ocr.name == "TESTOCR"

    def test_pre_process_default_returns_unchanged(self):
        """DEFAULT 模式 pre_process 不改变图像"""
        from module.atom.ocr import RuleOcr

        ocr = RuleOcr(
            name="TestOcr",
            mode="FULL",
            method="DEFAULT",
            roi=(0, 0, 100, 30),
            area=(0, 0, 100, 30),
            keyword="",
        )
        img = np.random.randint(0, 255, (30, 100, 3), dtype=np.uint8)
        result = ocr.pre_process(img)
        assert np.array_equal(result, img)

    def test_ocr_method_from_string(self):
        """OcrMethod 从字符串解析 method_type"""
        from module.ocr.base_ocr import OcrMethod, OcrMethodType

        m = OcrMethod("CF_RGB(FFFFFF,000000)")
        assert m.get_method_type() == OcrMethodType.CF_RGB
        assert m.get_val() == "FFFFFF,000000"

    def test_ocr_method_default(self):
        """OcrMethod 默认构造为 DEFAULT"""
        from module.ocr.base_ocr import OcrMethod, OcrMethodType

        m = OcrMethod()
        assert m.get_method_type() == OcrMethodType.DEFAULT
