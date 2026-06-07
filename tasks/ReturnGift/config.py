# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from pydantic import BaseModel, Field
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time


class ReturnGiftConfig(BaseModel):
    return_gift_timeout: Time = Field(default=Time(hour=0, minute=40, second=0), description='回礼限制运行时间')
    return_gift_mode: str = Field(default='gift', description='回礼运行模式：gift=送礼，sr_count=统计SR碎片')
    sr_template_date: str = Field(default='2026-06-07', description='生成SR模板时匹配截图文件名的日期')
    sr_temp_path: str = Field(default='temp_path', description='生成SR模板时读取截图的临时目录')
    sr_match_roi: tuple[int, int, int, int] = Field(default=(334, 163, 612, 454), description='SR模板匹配识别范围')
    sr_template_box: tuple[int, int, int, int] = Field(default=(340, 171, 104, 146), description='首个SR图像模板裁剪区域')
    sr_count_ocr_box: tuple[int, int, int, int] = Field(default=(348, 320, 95, 22), description='首个SR碎片数量OCR区域')
    sr_empty_swipe_limit: int = Field(default=3, description='连续未识别到新SR资源时停止滑动的次数')
    sr_match_threshold: float = Field(default=0.85, description='SR模板匹配阈值')


class ReturnGift(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    return_gift_config: ReturnGiftConfig = Field(default_factory=ReturnGiftConfig)

