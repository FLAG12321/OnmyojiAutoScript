# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import time
import cv2
import numpy as np
from PIL import Image
from cached_property import cached_property
from datetime import timedelta, datetime
from typing import List
from module.base.timer import Timer
from module.atom.image_grid import ImageGrid
from module.logger import logger
from module.exception import TaskEnd

from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_guild , page_team,page_mall
from tasks.GameUi.assets import GameUiAssets
from tasks.ReturnGift.assets import ReturnGiftAssets
from tasks.ReturnGift.config import ReturnGiftConfig
import random
import json
from pathlib import Path

from module.base.utils import point2str

SrMatch = tuple[float, int, int, int, int]


def parse_sr_count(text) -> int | None:
    # 解析OCR返回值中的SR碎片数量：DigitCounter返回元组(current, remain, total)时取第一个元素
    if text is None:
        return None
    if isinstance(text, tuple):
        return int(text[0]) if len(text) > 0 else None
    text = str(text)
    if not text:
        return None
    target = text.split('/', 1)[0]
    match = re.search(r'\d+', target)
    if match:
        return int(match.group())
    match = re.search(r'\d+', text)
    if match:
        return int(match.group())
    return None


def offset_box_by_match(
    base_ocr_box: tuple[int, int, int, int],
    anchor_box: tuple[int, int, int, int],
    matched_box: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    # 根据模板命中偏移推导同一卡片的碎片数量OCR区域
    dx = matched_box[0] - anchor_box[0]
    dy = matched_box[1] - anchor_box[1]
    return (base_ocr_box[0] + dx, base_ocr_box[1] + dy, base_ocr_box[2], base_ocr_box[3])


def sort_sr_matches(matches: list[SrMatch]) -> list[SrMatch]:
    # 固定按行列顺序处理匹配结果，保证输出稳定
    return sorted(matches, key=lambda item: (item[2], item[1]))


class ScriptTask(GameUi,ReturnGiftAssets):

    def run(self):
        con = self.config.return_gift.return_gift_config
        mode = con.return_gift_mode
        if mode == 'gift':
            return self._run_gift_mode()
        if mode == 'sr_count':
            return self._run_sr_count_mode()
        raise ValueError(f'Unsupported ReturnGift mode: {mode}')

    def _run_gift_mode(self):
        con = self.config.return_gift
        total_send_btn_click_count = 0

        timeout_duration = timedelta(hours=con.return_gift_config.return_gift_timeout.hour,
                                    minutes=con.return_gift_config.return_gift_timeout.minute,
                                    seconds=con.return_gift_config.return_gift_timeout.second)
        retry_count = 0
        timeout=datetime.now()
        self.screenshot()
        if self.ui_get_current_page() != page_guild:
            self.ui_goto(page_guild)

        if not self._check_daily_running_with_returngift():
            logger.info("无运行的Daily实例，等待3分钟...")
            daily_started = self._wait_and_monitor_daily_start(180, 10)
            if not daily_started:
                logger.info("等待3分钟后无Daily运行，ReturnGift跳过")
                next_run_time = datetime.now().replace(hour=0, minute=19, second=30, microsecond=0) + timedelta(days=1)
                self.set_next_run(task='ReturnGift', target=next_run_time)
                raise TaskEnd('ReturnGift')

        check_timer = Timer(45).start()
        while 1:
            self.screenshot()

            if check_timer.reached():
                check_timer.reset()
                if self._check_daily_running_with_returngift():
                    logger.info("检测到Daily运行，保持循环等待")
                    continue
                else:
                    logger.info("无Daily运行，ReturnGift跳过")
                    break

            if retry_count >= 4:
                retry_count=0
                if self.ui_get_current_page() != page_guild:
                    self.ui_goto(page_main)
                    self.ui_goto(page_guild)
                    continue
            """ if datetime.now() - self.start_time >= timeout_duration  or datetime.now() > timeout + timedelta(minutes=3):
                break """
            if self.appear(self.I_R_SEND_FLAG):
                sendtimeout, send_btn_click_count = self.send_gift()
                total_send_btn_click_count += send_btn_click_count
                if sendtimeout:
                    timeout=sendtimeout
                receivetimeout=self.receive_gift()
                if receivetimeout:
                    timeout=receivetimeout
                while 1:
                    self.screenshot()
                    if self.appear_then_click(self.I_R_BACK_Y, interval=1):
                        continue
                    if self.appear(GameUiAssets.I_CHECK_GUILD):
                        break
                continue
            if self.appear_then_click(self.I_R_PAGE_GUILD,action=self.C_R_TOSEND_CLICK,interval=2):
                self.device.click_record_clear()
                time.sleep(1)
                continue
            retry_count += 1
        now = datetime.now()

        self.config.notifier.push(
            title='ReturnGift任务提醒',
            content=f'送礼按钮点击次数: {total_send_btn_click_count}'
        )

        next_run_time = now.replace(hour=0, minute=19, second=30, microsecond=0) + timedelta(days=1)
        self.set_next_run(task='ReturnGift', target=next_run_time)
        raise TaskEnd('ReturnGift')

    def _run_sr_count_mode(self):
        # 导航到回礼页面并统计碎片数量，模板资源使用预生成的静态文件
        self._goto_return_gift_page()
        self.count_sr_fragments()
        raise TaskEnd('ReturnGift')

    def return_gift_dir(self) -> Path:
        # ReturnGift运行产物固定存放在任务目录，避免受当前工作目录变化影响
        return Path(__file__).resolve().parent

    def sr_count_dir(self) -> Path:
        return self.return_gift_dir() / 'count'

    def sr_count_output_path(self) -> Path:
        # 统计结果放在 logs 目录，避免被 assets_extract 扫描到
        output = Path('logs') / 'sr_count.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        return output

    def build_sr_templates(self) -> list[dict]:
        con = self.config.return_gift.return_gift_config
        temp_dir = Path(con.sr_temp_path)
        count_dir = self.sr_count_dir()
        count_dir.mkdir(parents=True, exist_ok=True)

        screenshots = sorted(temp_dir.glob(f'{con.sr_template_date} *.png'))
        items = []
        roi_x, roi_y, roi_w, roi_h = con.sr_match_roi
        box_x, box_y, box_w, box_h = con.sr_template_box
        roi_right = roi_x + roi_w
        roi_bottom = roi_y + roi_h

        for screenshot in screenshots:
            # 只裁剪完全落在匹配范围和截图尺寸内的模板，避免生成无法复用的截图片段
            if box_x < roi_x or box_y < roi_y or box_x + box_w > roi_right or box_y + box_h > roi_bottom:
                logger.warning(f'SR模板裁剪框不在识别范围内，跳过: {screenshot}')
                continue
            with Image.open(screenshot) as image:
                if box_x + box_w > image.width or box_y + box_h > image.height:
                    logger.warning(f'SR模板裁剪框超出截图尺寸，跳过: {screenshot}')
                    continue
                crop = image.convert('RGB').crop((box_x, box_y, box_x + box_w, box_y + box_h))
            name = f'sr_{len(items) + 1}'
            file_name = f'{name}.png'
            crop.save(count_dir / file_name)
            items.append({
                'name': name,
                'file': f'count/{file_name}',
                'source': str(screenshot),
                'crop_box': [box_x, box_y, box_w, box_h],
                'match_roi': list(con.sr_match_roi),
            })

        manifest = {
            'created_at': datetime.now().isoformat(),
            'items': items,
        }
        with open(count_dir / 'image.json', 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        logger.info(f'生成SR模板数量: {len(items)}')
        return items

    def load_sr_templates(self) -> list[RuleImage]:
        # 读取标准 image.json 格式的 SR 模板清单
        manifest_path = self.sr_count_dir() / 'image.json'
        if not manifest_path.exists():
            raise FileNotFoundError(f'SR模板清单不存在: {manifest_path}')
        with open(manifest_path, 'r', encoding='utf-8') as f:
            items = json.load(f)
        templates = []
        for item in items:
            template_path = self.sr_count_dir() / item['imageName']
            roi_front = tuple(int(x) for x in item['roiFront'].split(','))
            roi_back = tuple(int(x) for x in item['roiBack'].split(','))
            templates.append(RuleImage(
                roi_front=roi_front,
                roi_back=roi_back,
                threshold=item.get('threshold', 0.85),
                method=item.get('method', 'Template matching'),
                file=str(template_path),
            ))
        return templates

    def build_sr_count_ocr(self, ocr_box) -> RuleOcr:
        # 动态OCR区域跟随SR模板命中位置，不写入assets.py
        return RuleOcr(roi=tuple(ocr_box), area=tuple(ocr_box), mode='DigitCounter', method='Default', keyword='', name='sr_count')

    def swipe_sr_count_page(self):
        duration = 1
        p1 = (650, 520)
        p2 = (650, 352)
        logger.info('Swipe %s -> %s, %sS ' % (point2str(*p1), point2str(*p2), duration))
        self.device.swipe_adb(p1, p2, duration=duration)

    def write_sr_count_result(self, items: list[dict]) -> dict:
        result = {
            'updated_at': datetime.now().isoformat(),
            'total': len(items),
            'items': items,
        }
        with open(self.sr_count_output_path(), 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    def count_sr_fragments(self) -> dict:
        con = self.config.return_gift.return_gift_config
        templates = self.load_sr_templates()
        seen = set()
        items = []
        empty_swipes = 0

        while empty_swipes < con.sr_empty_swipe_limit and len(seen) < len(templates):
            self.screenshot()
            found_new = False
            for template in templates:
                template_name = Path(template.file).stem.lower()
                if template_name in seen:
                    continue
                matches = sort_sr_matches(template.match_all_any(
                    image=self.device.image,
                    roi=list(con.sr_match_roi),
                    threshold=con.sr_match_threshold,
                ))
                if not matches:
                    continue
                score, x, y, w, h = matches[0]
                match_box = (x, y, w, h)
                ocr_box = offset_box_by_match(con.sr_count_ocr_box, con.sr_template_box, match_box)
                ocr = self.build_sr_count_ocr(ocr_box)
                count = None
                ocr_text = ''
                for _ in range(2):
                    # 同一命中先重试OCR，避免短暂识别失败导致当前可见SR被滑过
                    ocr_text = ocr.ocr(self.device.image)
                    count = parse_sr_count(ocr_text)
                    if count is not None:
                        break
                if count is None:
                    logger.warning(f'SR碎片数量OCR解析失败: {template_name}, text={ocr_text}')
                    continue
                seen.add(template_name)
                found_new = True
                items.append({
                    'name': template_name,
                    'template': f'count/{template_name}.png',
                    'count': count,
                    'match_box': [x, y, w, h],
                    'ocr_box': list(ocr_box),
                    'ocr_text': ocr_text,
                })
            if found_new:
                empty_swipes = 0
                continue
            self.swipe_sr_count_page()
            empty_swipes += 1

        return self.write_sr_count_result(items)

    def _goto_return_gift_page(self):
        # 统计模式只进入回礼页面，不执行送礼、收礼或Daily联动逻辑
        self.screenshot()
        if self.ui_get_current_page() != page_guild:
            self.ui_goto(page_guild)
        retry_count = 0
        while retry_count < 5:
            self.screenshot()
            if self.appear_then_click(self.I_R_PAGE_GUILD, action=self.C_R_TOSEND_CLICK, interval=2):
                self.device.click_record_clear()
                time.sleep(1)
                return
            retry_count += 1
        raise RuntimeError('无法进入ReturnGift回礼页面')

    def position_offset(self, src, offset: tuple):
        return (src[0] + offset[0], src[1] + offset[1]
                , src[2] + offset[2], src[3] + offset[3])
    def is_piece_flag_equal_to_two(self, piece_flag: str) -> bool:
        """
        判断OCR识别结果是否包含数字2且不包含数字4
        """
        if not piece_flag:
            return False
            
        cleaned_flag = piece_flag.strip()
        return '2' in cleaned_flag and '4' not in cleaned_flag
    def get_roi_back_list(self):
        self.screenshot()
        # 获取所有匹配结果并直接转换为所需格式
        raw_matches = self.I_R_SEND_BTN.match_all_any(image=self.device.image, roi=[794,71,203,507])
        # 直接从匹配结果中提取坐标信息并按y坐标排序
        bounty_list = sorted(
            [[x, y, w, h] for (sc, x, y, w, h) in raw_matches],
            key=lambda item: item[1]  # 按y坐标排序
        )
        roilist=[]
        for idx, item in enumerate(bounty_list):
            self.O_PIECE_FLAG.roi = self.position_offset((item[0], item[1], 90, 50), (-215, 17, 0, 0))
            piece_flag = self.O_PIECE_FLAG.ocr(self.device.image)
            logger.info(f'piece_flag: {piece_flag}')
            if self.is_piece_flag_equal_to_two(piece_flag):
                roilist.append(list(self.position_offset((item[0], item[1], 200, 110), (-68, -43, 0, 0))))
        logger.info(f'匹配到的ROI列表: {roilist}')
        return roilist
    
    def send_gift(self):
        send_time=False
        send_btn_click_count = 0
        
        swipe_count = 0
        logger.info('开始送礼')
        while 1:
            retry_count = 0
            while 1:
                self.screenshot()
                if retry_count >= 3:
                    break 
                if self.appear_then_click(self.I_R_AWARD,action=self.C_R_AWARD_CLICK,interval=0.5):
                    retry_count = 0
                    self.device.click_record_clear()
                    continue
                roilist = self.get_roi_back_list()
                if not roilist:
                    retry_count +=1
                    continue
                self.I_R_SEND_BTN.roi_back = list(roilist[0]) 
                if self.appear_then_click(self.I_R_SEND_BTN, interval=0.5):
                    send_time=datetime.now()
                    self.device.click_record_clear()
                    send_btn_click_count += 1
                    retry_count = 0
                    continue
                retry_count +=1
                
            if  swipe_count >=2:
                break
            duration = 2
            safe_pos_x = random.randint(980, 1080)
            safe_pos_y = random.randint(500, 520)
            p1 = (safe_pos_x, safe_pos_y)
            p2 = (safe_pos_x, safe_pos_y - 300)
            logger.info('Swipe %s -> %s, %sS ' % (point2str(*p1), point2str(*p2), duration))
            self.device.swipe_adb(p1, p2, duration=duration)
            swipe_count += 1
       
        return send_time, send_btn_click_count
    def receive_gift(self):
        send_time=False
        retry_count = 0
        while 1:
            self.screenshot()
            if retry_count > 5:
                break
            if self.appear_then_click(self.I_R_AWARD,action=self.C_R_AWARD_CLICK, interval=0.5):
                time.sleep(1)
                self.screenshot()
                self.appear_then_click(self.I_R_AWARD,action=self.C_R_AWARD_CLICK, interval=0.5)
                break
            if self.appear_then_click(self.I_R_RECEIVE_ENSURE, interval=0.5):
                retry_count = 0
                continue
            if self.appear_then_click(self.I_R_RECEIVE_BTN, interval=0.5):
                send_time=datetime.now()
                retry_count = 0
                continue
            if self.appear_then_click(self.I_R_TORECEIVE_FLAG2,action=self.C_R_TORECEIVE_FLAG2_CLICK, interval=0.5):
                retry_count = 0
                continue
            if not self.appear(self.I_R_TORECEIVE_FLAG2) and self.appear_then_click(self.I_R_TORECEIVE_FLAG,action=self.C_R_TORECEIVE_FLAG_CLICK, interval=1.5):
                retry_count = 0
                continue
            retry_count += 1
        while 1:
            self.screenshot()
            if retry_count > 5:
                self.ui_goto(page_guild)
                break
            if self.appear_then_click(self.I_R_BACK_RED, interval=0.5):
                retry_count = 0
                continue
            if self.appear(self.I_R_SEND_FLAG, interval=0.5):
                break
            retry_count += 1
        return send_time
    def _check_daily_running_with_returngift(self) -> bool:
        """检查是否有 total_returngift_enable=true 的Daily正在运行"""
        progress_file = Path('./logs/daily_progress.json')
        if not progress_file.exists():
            return False

        with open(progress_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for key, value in data.items():
            if value.get('status') != 'running':
                continue
            if value.get('returngift_enable', False):
                return True

        return False

    def _wait_and_monitor_daily_start(self, max_seconds=180, interval=10) -> bool:
        """等待并监控Daily启动，返回True=Daily已启动"""
        check_count = max_seconds // interval
        for i in range(check_count):
            if self._check_daily_running_with_returngift():
                logger.info("检测到Daily运行")
                return True
            logger.info(f"等待Daily启动... ({i * interval}/{max_seconds}s)")
            time.sleep(interval)
        return False
            
            


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    c = Config('OAS1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.send_gift()
