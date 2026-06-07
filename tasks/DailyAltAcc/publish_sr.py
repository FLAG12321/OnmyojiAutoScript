# This Python file uses the following encoding: utf-8
import json
import time
from pathlib import Path

from module.base.utils import point2str
from module.logger import logger
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.ReturnGift.assets import ReturnGiftAssets
from tasks.ReturnGift.script_task import ScriptTask as ReturnGiftScriptTask

class PublishSr(DailyAltAccBase, ReturnGiftAssets):
    """发布SR碎片子功能：依据 sr_count.json 生成可发布队列，按序点击发布"""

    # 输入：碎片数量统计
    SR_COUNT_FILE = Path('logs/sr_count.json')
    # 输出/续做：可发布次数队列
    SR_CNT_FILE = Path(__file__).resolve().parent / 'sr_cnt.json'
    # 一次发布需要消耗的碎片数
    PER_PUBLISH = 99

    def run_publish_sr(self):
        """发布SR碎片入口：已有队列则续做，否则从统计文件构建"""
        if self.SR_CNT_FILE.exists():
            queue = self._read_queue()
        else:
            queue = self._build_queue_from_sr_count()
            self._write_queue(queue)
        ReturnGiftScriptTask._goto_return_gift_page(self)
        self._publish_loop(queue)

    def _build_queue_from_sr_count(self) -> list[dict]:
        """读取 logs/sr_count.json，将 count 转换为可发布次数(count // 99)"""
        if not self.SR_COUNT_FILE.exists():
            logger.warning(f'{self.SR_COUNT_FILE} 不存在，发布队列为空')
            return []
        with open(self.SR_COUNT_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        items = []
        for entry in raw:
            publish_times = int(entry['count']) // self.PER_PUBLISH
            if publish_times <= 0:
                continue
            items.append({'name': entry['name'], 'count': publish_times})
        return self._sort_queue(items)

    def _sort_queue(self, queue: list[dict]) -> list[dict]:
        """按 count 降序排序；count 相同时原列表中靠后的项排在前面"""
        indexed = list(enumerate(queue))
        indexed.sort(key=lambda iv: (-iv[1]['count'], -iv[0]))
        return [v for _, v in indexed]

    def _publish_loop(self, queue: list[dict]):
        """发布主循环：遍历队列匹配模板 → 点击 → 发布 → 递减 → 回写；未命中时滑动翻页直到连续空滑到底"""
        EMPTY_SWIPE_LIMIT = 3
        empty_swipes = 0
        while queue:
            matched_index = self._find_first_match(queue)
            if matched_index is None:
                empty_swipes += 1
                if empty_swipes >= EMPTY_SWIPE_LIMIT:
                    logger.info('连续滑动未命中，已到底部，结束发布流程')
                    break
                self._swipe_page()
                continue

            empty_swipes = 0
            top = queue[matched_index]
            if self._do_publish_sr(top['name']):
                top['count'] -= 1
                if top['count'] <= 0:
                    queue.pop(matched_index)
                queue = self._sort_queue(queue)
                self._write_queue(queue)
                # 发布成功后重新进入碎片页，从顶部开始匹配
                ReturnGiftScriptTask._goto_return_gift_page(self)
            else:
                logger.info(f'发布 SR 碎片: {top["name"]} 失败')

    def _find_first_match(self, queue: list[dict]) -> int | None:
        """截图一次后遍历队列，返回首个命中模板的索引；全部未命中返回 None"""
        self.screenshot()
        for idx, entry in enumerate(queue):
            rule = getattr(ReturnGiftAssets, entry['name'], None)
            if rule is None:
                logger.warning(f'ReturnGiftAssets 中找不到 {entry["name"]}，跳过')
                continue
            if self.appear_then_click(rule):
                return idx
        return None

    def _swipe_page(self):
        """向下滑动碎片列表页"""
        self.device.click_record_clear()
        p1 = (650, 520)
        p2 = (650, 350)
        logger.info('Swipe %s -> %s, 2S' % (point2str(*p1), point2str(*p2)))
        self.device.swipe_adb(p1, p2, duration=2)

    def _do_publish_sr(self, name: str):
        """执行单次SR碎片发布"""
        self.screenshot()
        rule = getattr(self, name, None)
        if rule is None:
            logger.warning(f'找不到资源 {name}，跳过发布')
            return False
        logger.info(f'发布 SR 碎片: {name}')
        start_time = time.time()
        while (time.time()-start_time < 5):
            self.screenshot()
            if self.appear(self.I_PAGE_PUBLISH):
                logger.info(f'选择发布个数: {name}')
                break
            if self.appear_then_click(rule,interval=1):
                continue
        if time.time()-start_time >= 5:
            return False
        start_time = time.time()
        while (time.time()-start_time < 5):
            self.screenshot()
            if self.ocr_appear(self.O_CHECK_COUNT) and not self.appear(self.I_PUBLISH_ENSURE2):
                break
            if self.ocr_appear_click(self.O_CHECK_COUNT,action=self.I_PUBLISH_ENSURE2,interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_ADD_COUNT,interval=1):
                start_time = time.time()
                continue
            if self.appear_then_click(self.I_TO_SELECT_COUNT,interval=1):
                start_time = time.time()
                continue
        if time.time()-start_time >= 5:
            return False
        start_time = time.time()
        while (time.time()-start_time < 5):
            self.screenshot()
            if not self.appear(self.I_PAGE_PUBLISH) and not self.appear(self.I_PUBLISH_ENSURE):
                logger.info(f'发布 SR 碎片: {name} 成功')
                break
            if self.appear_then_click(self.I_PUBLISH_ENSURE,interval=1):
                start_time = time.time()
                continue
        if time.time()-start_time >= 5:
            return False
        return True
        
        
    def _read_queue(self) -> list[dict]:
        """读取运行期队列文件"""
        with open(self.SR_CNT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _write_queue(self, queue: list[dict]):
        """写入运行期队列文件，支持断点续做"""
        self.SR_CNT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.SR_CNT_FILE, 'w', encoding='utf-8') as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
