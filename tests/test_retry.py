"""retry 装饰器单测：验证重试语义、边界与设计前提。

pytest -q 应全部通过。测试不依赖网络，也不真的等待——退避用 monkeypatch 拦截。
"""
import pytest

from app.utils.retry import retry


def test_retry_succeeds_after_transient_failures():
    """原有的 flaky 场景：前两次超时、第三次成功，返回值和调用次数都要对。"""
    calls = {"n": 0}

    @retry(times=3, base_delay=0.001, exceptions=(TimeoutError,))
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("模拟超时")
        return "第三次成功"

    assert flaky() == "第三次成功"
    assert calls["n"] == 3


def test_retry_reraises_last_exception_after_exhaustion():
    """重试耗尽：抛出最后一次异常，且调用次数恰好等于 times（不多不少）。"""
    calls = {"n": 0}

    @retry(times=3, base_delay=0.001, exceptions=(TimeoutError,))
    def always_fail():
        calls["n"] += 1
        raise TimeoutError(f"第 {calls['n']} 次超时")

    with pytest.raises(TimeoutError, match="第 3 次超时"):
        always_fail()
    assert calls["n"] == 3


def test_retry_does_not_catch_unlisted_exceptions():
    """设计前提：业务异常不该重试。只调一次就原样冒出，避免放大错误。"""
    calls = {"n": 0}

    @retry(times=5, base_delay=0.001, exceptions=(TimeoutError,))
    def business_error():
        calls["n"] += 1
        raise ValueError("业务规则不通过")

    with pytest.raises(ValueError, match="业务规则不通过"):
        business_error()
    assert calls["n"] == 1


def test_retry_skips_sleep_when_first_call_succeeds(monkeypatch):
    """一次成功时不应有任何退避等待，否则平白拖慢正常请求。"""
    sleeps = []
    monkeypatch.setattr("app.utils.retry.time.sleep", lambda s: sleeps.append(s))

    @retry(times=3, base_delay=0.5)
    def ok():
        return "ok"

    assert ok() == "ok"
    assert sleeps == []


def test_retry_uses_exponential_backoff_capped_by_max_delay(monkeypatch):
    """退避按 2^(n-1) 增长，带抖动，且受 max_delay 截断。

    把随机抖动 patch 成 0，才能对延迟做精确断言；真实抖动是防止
    多个客户端同时重试打爆下游，不能去掉，只是测试里不测它。
    """
    sleeps = []
    monkeypatch.setattr("app.utils.retry.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("app.utils.retry.random.uniform", lambda lo, hi: 0.0)

    calls = {"n": 0}

    @retry(times=4, base_delay=0.5, max_delay=1.2, exceptions=(TimeoutError,))
    def flaky():
        calls["n"] += 1
        if calls["n"] < 4:
            raise TimeoutError("超时")
        return "done"

    assert flaky() == "done"
    # 第 3 次理论值 0.5*2^2=2.0，被 max_delay=1.2 截断
    assert sleeps == [0.5, 1.0, 1.2]


def test_retry_preserves_function_metadata():
    """functools.wraps 保留原函数名，日志里才能看出是哪个调用在重试。"""
    @retry(times=2)
    def named_func():
        return None

    assert named_func.__name__ == "named_func"
