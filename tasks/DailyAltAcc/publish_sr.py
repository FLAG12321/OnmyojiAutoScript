# This Python file uses the following encoding: utf-8
import json
from pathlib import Path

from module.logger import logger
from tasks.DailyAltAcc.utils import DailyAltAccBase
from tasks.ReturnGift.assets import ReturnGiftAssets


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
        """发布主循环：遍历队列匹配模板 → 点击 → 发布 → 递减 → 回写"""
        while queue:
            matched_index = self._find_first_match(queue)
            if matched_index is None:
                # 队列中所有模板均未命中，退出
                logger.info('队列中所有 SR 模板均未命中，结束发布流程')
                break

            top = queue[matched_index]
            self._do_publish_sr(top['name'])

            top['count'] -= 1
            if top['count'] <= 0:
                queue.pop(matched_index)
            queue = self._sort_queue(queue)
            self._write_queue(queue)

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

    def _do_publish_sr(self, name: str):
        """预留发布接口，当前仅日志占位"""
        logger.info(f'TODO publish {name}')

    def _read_queue(self) -> list[dict]:
        """读取运行期队列文件"""
        with open(self.SR_CNT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _write_queue(self, queue: list[dict]):
        """写入运行期队列文件，支持断点续做"""
        self.SR_CNT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.SR_CNT_FILE, 'w', encoding='utf-8') as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
