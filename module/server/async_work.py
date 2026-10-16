"""后台执行服务端同步操作，取消时先等待实际操作收尾。"""

import asyncio


async def run_blocking(function, *args, **kwargs):
    """释放事件循环，同时保证调用方持有的生命周期锁不会提前释放。"""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # 取消 await 不能停止线程；等线程结束后才让外层释放身份/生命周期锁。
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        # 取消仍由原调用方处理，线程异常必须取走以免留下未消费的任务错误。
        if not worker.cancelled():
            worker.exception()
        raise
