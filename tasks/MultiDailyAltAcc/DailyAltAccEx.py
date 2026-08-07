from tasks.DailyAltAcc.config import  DailyAltAccConfig,GeneralBattleConfig,DailyAltAcc
from module.logger import logger
def get_config(self):
    config=DailyAltAcc()
    config.daily_alt_acc_config.alliedteam_battle_enable = self.account_info.alliedteam_battle_enable
    config.daily_alt_acc_config.alliedteam_ap_enable = self.account_info.alliedteam_ap_enable
    config.daily_alt_acc_config.mail_enable = self.account_info.mail_enable
    config.daily_alt_acc_config.donatejade_enable = self.account_info.donatejade_enable
    config.daily_alt_acc_config.courtyard_enable = self.account_info.courtyard_enable
    config.daily_alt_acc_config.alliedteam_limit_count = self.account_info.alliedteam_limit_count
    config.daily_alt_acc_config.alliedteam_invite_count = self.account_info.alliedteam_invite_count
    config.daily_alt_acc_config.cooperation_enable = self.account_info.cooperation_enable
    config.daily_alt_acc_config.returngift_enable = self.account_info.returngift_enable
    config.daily_alt_acc_config.weekaward_enable = self.account_info.weekaward_enable
    config.daily_alt_acc_config.mysteryshop_enable = self.account_info.mysteryshop_enable
    config.daily_alt_acc_config.isflower = self.account_info.isflower
    config.daily_alt_acc_config.kekkaiActivation_enable = self.account_info.kekkaiActivation_enable
    config.daily_alt_acc_config.KekkaiUtilize_enable = self.account_info.KekkaiUtilize_enable
    config.daily_alt_acc_config.tree_planting_enable = self.account_info.tree_planting_enable
    config.daily_alt_acc_config.trialbattle_enable = self.account_info.trialbattle_enable
    config.daily_alt_acc_config.summon_up_enable = self.account_info.summon_up_enable
    config.daily_alt_acc_config.publish_sr_enable = self.account_info.publish_sr_enable
    return config
def run_success(self):
    self.daily_conf.update_account_login_history
    self.config.save()
