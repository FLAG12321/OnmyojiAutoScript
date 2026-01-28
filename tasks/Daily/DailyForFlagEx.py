from tasks.DailyForFlag.config import  DailyForFlagConfig,GeneralBattleConfig,DailyForFlag
from module.logger import logger
def get_config(self):
    config=DailyForFlag()
    config.daily_for_flag_config.tongxin_battle_enable = self.account_info.tongxin_battle_enable
    config.daily_for_flag_config.tongxin_ap_enable = self.account_info.tongxin_ap_enable
    config.daily_for_flag_config.mail_enable = self.account_info.mail_enable
    config.daily_for_flag_config.juangou_enable = self.account_info.juangou_enable
    config.daily_for_flag_config.tingyuan_enable = self.account_info.tingyuan_enable
    config.daily_for_flag_config.tongxin_limit_count = self.account_info.tongxin_limit_count
    config.daily_for_flag_config.xiezuo_enable = self.account_info.xiezuo_enable
    config.daily_for_flag_config.huili_enable = self.account_info.huili_enable
    config.daily_for_flag_config.weekaward_enable = self.account_info.weekaward_enable
    config.daily_for_flag_config.mysteryshop_enable = self.account_info.mysteryshop_enable
    return config
def run_success(self):
    self.daily_conf.update_account_login_history
    self.config.save()
