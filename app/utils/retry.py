"""重试工具：只用于瞬时故障（网络抖动、超时、连接失败）"""
import functools
import logging
import random
import time

logger = logging.getLogger(__name__)


def retry(times: int = 3,
          base_delay: float = 0.5,
          max_delay: float = 8.0,
          exceptions: tuple = (Exception,)):
    """指数退避 + 随机抖动重试。

    用法：
        @retry(times=3, exceptions=(TimeoutError, ConnectionError))
        def call_llm(prompt): ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == times:
                        break
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.uniform(0, delay * 0.3)   # 抖动，防止同时重试打爆下游
                    logger.warning(
                        "第 %s/%s 次调用失败：%s，%.2fs 后重试",
                        attempt, times, exc, delay,
                    )
                    time.sleep(delay)
            logger.error("重试 %s 次后仍失败：%s", times, last_exc)
            raise last_exc
        return wrapper
    return decorator