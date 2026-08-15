# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
#
import re
from pathlib import Path
from PySide6.QtCore import QObject, Slot, Signal

from module.config.config_store import ConfigStore
from module.logger import logger

# 震惊到我姥姥家 除了第一个函数all_script_files是我自己写的
# 后面的都是github copilot写的
class Add(QObject):

    def __init__(self) -> None:
        super(Add, self).__init__()
        self.store = ConfigStore(config_root=Path.cwd() / 'config')

    @Slot(result="QVariantList")
    def all_script_files(self) -> list:
        """
        获取所有的脚本文件 除了tmplate
        :return: ['oas1', 'oas2']
        """
        return self.store.active_config_names()

    @Slot(result="QVariantList")
    def all_json_file(self) -> list:
        """
        获取所有的json文件
        :return: ['oas1', 'oas2']
        """
        result = self.store.active_config_names(include_template=True)
        if 'template' in result:
            result.remove('template')
            result.insert(0, 'template')
        return result


    @Slot(str, str)
    def copy(self, file: str, template: str = 'template') -> None:
        """
        复制一个配置文件
        :param file:  不带json后缀
        :param template:
        :return:
        """
        try:
            canonical = self.store.load(template).canonical
            self.store.create_from_template(file, canonical)
            logger.info(f'copy {template} to {file}')
        except TimeoutError as e:
            # Qt Slot 不能向事件循环抛异常，但锁超时是「稍后重试即可」，
            # 必须与「名称非法/已存在」区分开，否则界面上只表现为点了没反应。
            logger.error(f'copy {template} to {file} failed: config is locked by another process, retry later: {e}')
        except Exception as e:
            logger.error(f'copy {template} to {file} failed: {type(e).__name__}: {e}')


    @Slot(result="QString")
    def generate_script_name(self) -> str:
        """
        生成一个新的配置的名字
        :return:
        """
        all_script_files = self.all_script_files()
        if not all_script_files:
            return 'oas1'

        script_numbers = []
        for script_file in all_script_files:
            match = re.search(r'\d+', script_file)
            if match:
                script_number = int(match.group())
                script_numbers.append(script_number)

        if not script_numbers:
            return 'oas1'
        script_numbers.sort()
        new_script_number = script_numbers[-1] + 1
        return f'oas{new_script_number}'

if __name__ == "__main__":
    a = Add()
    print(a.all_script_files())
    print(a.all_json_file())
    print(a.generate_script_name())


