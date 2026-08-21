# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import json
import threading
from fastapi import APIRouter, Body
from pathlib import Path

from module.config.utils import write_file
from module.logger import logger
from module.ocr.rpc import shutdown_ocr_server
from module.server.main_manager import MainManager
from module.server.updater import Updater, _update_progress
from module.server.i18n import I18n

home_app = APIRouter(
    prefix="/home",
    tags=["home"],
)


@home_app.get('/test')
async def home_test():
    return {'message': 'test'}


#  gcc -Wall -pedantic -shared -fPIC -o group_work.so group_work.c -lwiringPi
@home_app.get('/home_menu')
async def home_menu():
    return {'Home': [], 'Updater': [], 'Tool': []}


@home_app.post('/notify_test')
async def notify_test(setting: str, title: str, content: str):
    from module.notify.notify import Notifier
    try:
        notifier = Notifier(setting, True)
        if notifier.push(title=title, content=content):
            del notifier
            return True
        else:
            del notifier
            return False
    except Exception as e:
        logger.exception(e)
        return str(e)


@home_app.get('/kill_server')
async def kill_server():
    shutdown_ocr_server()
    MainManager.signal_kill_server = True
    return 'success'


@home_app.get('/update_info')
def update_info():
    # 同步 def：FastAPI 会放到线程池执行，git fetch 耗时不会阻塞事件循环。
    # 即便连不上 GitHub，也不至于拖住 /update_progress 等其他接口。
    try:
        updater = Updater()
        result = {'is_update': updater.check_update(),
                  'fetch_ok': updater.fetch_ok,
                  'branch': updater.current_branch(),
                  'repository': updater.Repository,
                  'current_commit': updater.current_commit(),
                  'latest_commit': updater.latest_commit(),
                  'commit': updater.get_commit(n=15),
                  }
        return result
    except Exception as e:
        logger.error(e)
        return None


@home_app.get('/execute_update')
async def execute_update():
    # 防重入：running 时拒绝再次触发，避免两个线程同时跑 git 互相踩锁。
    # failed / rejected / done / idle 都允许重试 —— 这就是「更新中断后一键恢复」的入口，
    # execute_pull 全程幂等，重跑会接上上次中断后的剩余阶段。
    if _update_progress.snapshot()['status'] == 'running':
        return '更新正在后台运行，请等待本次更新完成或失败后再重试'
    try:
        updater = Updater()

        def _run():
            # 线程级兜底：任何未捕获异常都不让状态卡死在 running，
            # 否则前端永远无法再次触发更新（恢复入口被堵死）。
            try:
                updater.execute_pull()
            except Exception as e:
                logger.error(e)
                _update_progress.finish(False)

        threading.Thread(target=_run, daemon=True).start()
    except Exception as e:
        logger.error(e)
    return '更新已在后台开始，可通过 /update_progress 查看进度'


@home_app.get('/update_progress')
async def update_progress():
    return _update_progress.snapshot()


@home_app.post('/update_config')
async def update_config(branch: str = None, repository: str = None):
    # 写回 deploy.yaml（DeployConfig.__setattr__ 自动落盘）；Repository 同步 git remote
    updater = Updater()
    if repository:
        repository = str(repository).strip()
        if not (repository.startswith('http://') or repository.startswith('https://')
                or repository.startswith('git@')):
            return {'error': 'repository 格式不合法'}
        if repository != updater.Repository:
            updater.Repository = repository
        # 无条件同步 origin：即使表单值与 deploy.yaml 一致（如用户直接改过 yaml），
        # .git/config 的 origin 仍可能是旧地址，必须 set-url 才能让拉取真正换源
        if not updater.execute_stream(f'"{updater.git}" remote set-url origin {repository}'):
            logger.warning('git remote set-url origin failed')
    if branch:
        updater.Branch = str(branch).strip()
    return {'repository': updater.Repository, 'branch': updater.Branch}


@home_app.put('/chinese_translate')
async def chinese_translate(data: dict = Body(...)):
    try:
        I18n.save_zh_cn(data)
    except Exception as e:
        logger.error(e)
    return True


@home_app.get('/additional_translate')
async def additional_translate() -> dict:
    try:
        data = I18n.load_additions()
        return data
    except Exception as e:
        logger.error(e)
    # 异常时也保证响应形状稳定（两语言字段齐全），避免前端解析中断
    return {'en-US': {}, 'zh-CN': {}}
