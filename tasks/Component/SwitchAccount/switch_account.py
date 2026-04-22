from adbutils import device

from module.config.config import Config
from module.device.device import Device
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.exit_game import ExitGame
from tasks.Component.SwitchAccount.login_account import LoginAccount
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_login
from tasks.base_task import BaseTask
from tasks.Restart.login import LoginHandler
import numpy as np
from module.atom.ocr import RuleOcr
import cv2
from module.logger import logger

def _prepare_image_for_ocr(image: np.ndarray, asset: RuleOcr) -> np.ndarray:
    """
    入参：image(np.ndarray/cv2 BGR格式)、asset(RuleOcr含ROI)
    返回：np.ndarray处理后图片
    核心逻辑：仅保留ROI内邮箱区域，擦除该区域内其他所有内容，原图非ROI区域不变
    """
    # 深拷贝原图，避免修改原始输入数据
    img_process = image.copy()
    # 提取ROI坐标并裁剪出目标区域（仅处理ROI内内容）
    roi_x, roi_y, roi_w, roi_h = asset.roi
    roi_area = img_process[roi_y:roi_y+roi_h, roi_x:roi_x+roi_w]
    
    # 防护：若ROI裁剪后为空，直接返回原图
    if roi_area.size == 0:
        return img_process

    # 1. ROI转灰度图（兼容3通道BGR/单通道灰度图）
    if len(roi_area.shape) == 3 and roi_area.shape[2] == 3:
        gray = cv2.cvtColor(roi_area, cv2.COLOR_BGR2GRAY)
    else:
        gray = roi_area

    # 2. OTSU自动阈值反相二值化（自适应亮度，精准提取文字轮廓）
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    # 3. 横向膨胀（连接邮箱零散文字轮廓，确保检测到完整邮箱区域）
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (6, 2))
    binary_dilate = cv2.dilate(binary, kernel, iterations=1)

    # 4. 查找外部轮廓（兼容所有OpenCV版本）
    contours, _ = cv2.findContours(binary_dilate, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 5. 先将整个ROI填充为白色（擦除所有内容）
    roi_area[:] = (255, 255, 255)

    # 6. 筛选邮箱轮廓，将原邮箱内容还原回去（仅保留邮箱）
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # 邮箱特征：水平长条形（可根据实际图片微调）
        if w >= 30 and h >= 8 and (w / h) >= 4:
            # 从原始ROI中截取邮箱区域，还原到白色ROI中
            roi_area[y:y+h, x:x+w] = image[roi_y+y:roi_y+y+h, roi_x+x:roi_x+x+w]

    # 7. 将处理后的ROI写回原图，返回结果
    img_process[roi_y:roi_y+roi_h, roi_x:roi_x+roi_w] = roi_area
    return img_process

class SwitchAccount(LoginAccount, ExitGame, GameUi, SwitchAccountAssets):

    def __init__(self, config: Config, device: Device, to: AccountInfo, frm: AccountInfo = None):
        """

        @param config:
        @type config:
        @param device:
        @type device:
        @param to: 要登录的账号信息
        @type to:
        @param frm: 上一个账号信息 ,避免关键字from
        @type frm:
        """
        super().__init__(config, device)
        self.to_account_info = to
        self.from_account_info = frm

    def switchAccount(self):
        logger.info("start switchAccount %s-%s", self.to_account_info.character, self.to_account_info.svr)
        # 判断所处界面
        curPage = self.ui_get_current_page()

        if curPage != page_login and curPage != page_main:
            self.ui_goto(page_main)
            curPage = self.ui_get_current_page()
        if curPage == page_main:
            self.exitGame()

        # 处于登录界面
        if not self.login(self.to_account_info):
            return False
        logger.info("%s login suc", self.to_account_info.character)
        # 处理位于登录界面各种奇葩弹窗
        login_handler = LoginHandler(config=self.config, device=self.device)
        login_handler.skip_onmyoji_genie = True
        login_handler.set_specific_usr(self.to_account_info.svr)
        login_handler.app_handle_login()

        return True


if __name__ == '__main__':
    config = Config('oas3')
    device=Device(config)
    toAccount=AccountInfo(account="email0@163.com", account_alias="emailO#emailo", apple_or_android=True, character="粘贴", svr="立秋夕烛")
    self=SwitchAccount(config,device,toAccount) 
    self.screenshot()
                    # 账号列表已打开状态
    ocrRes = self.O_SA_SELECT_SVR_CHARACTER_LIST.detect_and_ocr(self.device.image)
    #ocrRes = self.O_SA_ACCOUNT_ACCOUNT_LIST.detect_and_ocr(_prepare_image_for_ocr(self.device.image, asset=self.O_SA_ACCOUNT_ACCOUNT_LIST))
    
    """ # 找到该账号
    for index, ocr_account in enumerate([ocrResItem.ocr_text for ocrResItem in ocrRes]):
        ocrResItem = ocrRes[index]
        logger.info(f"ocrResItem.box {ocrResItem.box}, ocrResItem.ocr_text {ocrResItem.ocr_text}")
        ocrResBoxList = [ocrResItem.box for ocrResItem in ocrRes]
        roi_x = self.O_SA_ACCOUNT_ACCOUNT_LIST.roi[0] + ocrResBoxList[index][0][0]
        roi_y = self.O_SA_ACCOUNT_ACCOUNT_LIST.roi[1] + ocrResBoxList[index][0][1]
        roi_width = ocrResBoxList[index][1][0] - ocrResBoxList[index][0][0]
        roi_height = ocrResBoxList[index][2][1] - ocrResBoxList[index][1][1]
        
        # 创建一个较小的点击区域，确保点击在文字中心附近
        # 避免点击到相邻账号区域
        click_roi = [
            roi_x + roi_width * 0.2,  # 从左边20%处开始
            roi_y + roi_height * 0.2, # 从上边20%处开始
            roi_width * 0.6,          # 使用60%的宽度
            roi_height * 0.6          # 使用60%的高度
        ]
        logger.info(f"ocrResBoxList roi ({roi_x}, {roi_y}, {roi_width}, {roi_height})")
        logger.info(f"click_roi {click_roi}") """
    """     
    prepared_image=_prepare_image_for_ocr(sa.device.image, asset=sa.O_SA_ACCOUNT_ACCOUNT_LIST)
        prepared_image.save("prepared_image.png")
        ocrRes = sa.O_SA_ACCOUNT_ACCOUNT_LIST.detect_and_ocr(_prepare_image_for_ocr(sa.device.image, asset=sa.O_SA_ACCOUNT_ACCOUNT_LIST)) 
"""