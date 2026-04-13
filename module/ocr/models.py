from typing import Dict
from typing import List

from module.base.decorator import cached_property
# 根据项目需求选择合适的OCR实现
from module.ocr.onnx_paddle_ocr import ONNXPaddleOcr  # 使用ONNX PaddleOCR
from module.ocr.ppocr import TextSystem  # 保留TextSystem导入用于兼容性
from module.ocr.rpc import ModelProxy  # 导入ModelProxy类
from module.server.setting import State  # 导入State用于配置管理

import cv2
import time
import numpy as np
import sys

class OcrModel:
    @cached_property
    def ch(self):
        # 返回ONNX PaddleOCR实例，符合项目OCR库技术选型规范
        return ONNXPaddleOcr(use_angle_cls=True, use_gpu=False)

OCR_MODEL = OcrModel()

# 初始化OCR代理缓存
_OCR_PROXY_CACHE: Dict[str, ModelProxy] = {}


def get_ocr_model(lang: str = "ch"):
    """
    获取OCR模型实例，支持本地模型和远程服务
    根据配置决定使用本地模型还是RPC服务
    """
    deploy_config = State.deploy_config
    if deploy_config.UseOcrServer:
        address = deploy_config.OcrClientAddress or "127.0.0.1:22268"
        if address not in _OCR_PROXY_CACHE:
            _OCR_PROXY_CACHE[address] = ModelProxy(address)
        return _OCR_PROXY_CACHE[address]
    return getattr(OCR_MODEL, lang)


if __name__ == "__main__":
    model = OCR_MODEL.ch
    import time
    from memory_profiler import profile
    #image = cv2.imread(r"E:\Project\OnmyojiAutoScript-assets\jade.png")
    image = cv2.imread(r"d:\2025-07-17_12-52-22-184354.png")

    # 引入ocr 会导致非常巨大的内存开销
    @profile
    def test_memory():
        for i in range(2):
            start_time = time.time()
            #result = model.detect_and_ocr(image)
            result = model.ocr(image)
            print(result)
            end_time = time.time()
            print(f'耗时：{end_time-start_time}')

    test_memory()