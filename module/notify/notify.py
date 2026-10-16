# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import onepush.core
import threading
import yaml
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response
from smtplib import SMTPResponseException

from module.logger import logger
onepush.core.log = logger

# 单次通知推送的硬超时（秒）。正常推送秒级返回，15 秒足够覆盖慢网络；
# 超时只放弃这一次通知，不影响任务本身继续收尾。
PUSH_TIMEOUT = 15


class Notifier:
    def __init__(self, _config: str, enable: bool=False) -> None:
        self.config_name: str = ""
        self.enable: bool = enable

        if not self.enable:
            return
        config = {}
        try:
            for item in yaml.safe_load_all(_config):
                config.update(item)
        except Exception as e:
            logger.error("Fail to load onepush config, skip sending")
            return
        self.config = config
        try:
            # 获取provider
            self.provider_name: str = self.config.pop("provider", None)
            if self.provider_name is None:
                logger.info("No provider specified, skip sending")
                return
            # 获取notifier
            self.notifier: Provider = get_notifier(self.provider_name)
            # 获取notifier的必填参数
            self.required: list[str] = self.notifier.params["required"]
        except OnePushException:
            logger.exception("Init notifier failed")
            return
        except Exception as e:
            logger.exception(e)
            return

    def push(self, **kwargs) -> bool:
        if not self.enable:
            return False
        # 初始化不完整或必填项缺失不能被待发箱当作已送达。
        if not hasattr(self, 'notifier') or not hasattr(self, 'required'):
            return False
        # 默认在标题前拼接 config_name（保留空格），与既有全局行为一致；
        # 若调用方已自带完整标题（含 config 前缀，如协作汇总「小号1｜多账号日常完成」），
        # 可传 skip_config_prefix=True 跳过前缀拼接，不影响其他调用方。
        if not kwargs.pop("skip_config_prefix", False):
            kwargs["title"] = f"{self.config_name} {kwargs['title']}"
        self.config.update(kwargs)
        # pre check
        for key in self.required:
            if key not in self.config:
                logger.warning(
                    f"Notifier {self.notifier} require param '{key}' but not provided"
                )
                return False


        if isinstance(self.notifier, Custom):
            if "method" not in self.config or self.config["method"] == "post":
                self.config["datatype"] = "json"
            # 自定义通知未提供 data 时建立字典，避免短路条件访问不存在的键。
            if not isinstance(self.config.get("data"), dict):
                self.config["data"] = {}
            if "title" in kwargs:
                self.config["data"]["title"] = kwargs["title"]
            if "content" in kwargs:
                self.config["data"]["content"] = kwargs["content"]

        if self.provider_name.lower() == "gocqhttp":
            access_token = self.config.get("access_token")
            if access_token:
                self.config["token"] = access_token


        try:
            # 底层 requests 未设超时，用守护线程兜底，避免推送卡死拖垮整个任务
            resp = self._push_with_timeout(PUSH_TIMEOUT)
            if isinstance(resp, Response):
                # HTTP 接收成功后仍须核对服务商的业务状态，不能仅凭未抛异常确认。
                if not 200 <= resp.status_code < 300:
                    logger.warning("Push notify failed!")
                    logger.warning(f"HTTP Code:{resp.status_code}")
                    return False
                if self.provider_name.lower() == "pushplus":
                    if resp.json().get("code") != 200:
                        logger.warning("PushPlus 未确认接收通知，保留待重试")
                        return False
                elif self.provider_name.lower() == "gocqhttp":
                    data = resp.json()
                    if data.get("status") != "ok" or data.get("retcode", 0) != 0:
                        logger.warning("go-cqhttp 未确认发送通知，保留待重试")
                        return False
            elif self.provider_name.lower() == "smtp":
                # SMTP 空拒收字典才代表全部收件人已接收；部分拒收也保留重试。
                if not isinstance(resp, dict) or resp:
                    return False
            else:
                # onepush 会把 requests 网络异常吞成 None，未知返回值一律不能确认送达。
                logger.warning("通知没有有效响应，保留待重试")
                return False
        except TimeoutError:
            logger.warning(f"Push notify timeout after {PUSH_TIMEOUT}s, skip this notify")
            return False
        except SMTPResponseException:
            logger.warning("Appear SMTPResponseException")
            return False
        except OnePushException:
            logger.exception("Push notify failed")
            return False
        except Exception as e:
            logger.exception(e)
            return False

        logger.info("Push notify success")
        return True

    def _push_with_timeout(self, timeout: float):
        """在守护线程里执行一次推送，超时抛 TimeoutError。

        onepush 的 Provider 不透传 timeout（`notify(**kwargs)` 里多余的键会被
        `_prepare_data` 吞掉），底层请求始终是无超时的 requests 调用，推送服务端
        只建连不响应时任务线程会被永久阻塞。这里在调用侧兜底：拿不到结果就放弃
        这一次通知，让任务照常收尾（否则调度器连空闲关游戏的分支都走不到）。
        """
        result = {}
        # 超时的底层线程可能还在阻塞；闲时补发不能不断创建新线程积累连接。
        previous = getattr(self, '_push_thread', None)
        if previous is not None and previous.is_alive():
            raise TimeoutError('previous notify request is still running')

        def _worker():
            try:
                result['resp'] = self.notifier.notify(**self.config)
            except Exception as e:  # 异常带回主线程，交 push 里原有的分支处理
                result['error'] = e

        thread = threading.Thread(target=_worker, name='notify-push', daemon=True)
        self._push_thread = thread
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            # 工作线程还挂在等待对端响应，连着它的连接一起泄漏，随进程退出回收
            raise TimeoutError(f'push notify timeout after {timeout}s')
        if 'error' in result:
            raise result['error']
        return result.get('resp')


