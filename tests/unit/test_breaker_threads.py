"""锁定熔断器线程安全：HALF_OPEN「恰好一个 probe」保证在并发下成立。

CircuitBreaker/ProviderHealth 旧实现无锁，ingestion 工作线程与主循环并发
改全局单例：HALF_OPEN 状态下多线程同时进入 allow_request 都会读到
self._probe_in_flight=False 并把它设 True，多次返回 True → 多 probe
同时打穿半开闸门。ProviderHealth.get 多线程建 breaker 也存在竞态。
"""
import threading

from app.llm.base import (
    CircuitBreaker,
    CircuitState,
    provider_health,
)


def _breaker_in_half_open(cooldown: float = 0.0):
    """构造一个处于 HALF_OPEN 的熔断器。"""
    b = CircuitBreaker(failure_threshold=10, cooldown_seconds=cooldown)
    b.state = CircuitState.HALF_OPEN
    b._probe_in_flight = False
    return b


def test_half_open_admits_exactly_one_probe_under_contention():
    b = _breaker_in_half_open()
    barrier = threading.Barrier(20)
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        r = b.allow_request()
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1, \
        f"HALF_OPEN 并发下仅一个 probe 应放行：{results.count(True)} 次"


def test_provider_health_get_returns_same_breaker_across_threads():
    provider_name = "test_concurrent_provider_xyz"
    if provider_name in provider_health._breakers:
        del provider_health._breakers[provider_name]
    barrier = threading.Barrier(20)
    seen: list[CircuitBreaker] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        b = provider_health.get(provider_name)
        with lock:
            seen.append(b)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 20
    assert all(b is seen[0] for b in seen), "并发 get 必须返回同一 breaker 实例"


def test_breaker_state_transitions_serialized():
    b = _breaker_in_half_open()
    barrier = threading.Barrier(50)
    successes: list[bool] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        ok = b.allow_request()
        with lock:
            successes.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert successes.count(True) == 1, "50 并发 HALF_OPEN 仅允许 1 probe"
    assert b._probe_in_flight is True


def test_on_failure_does_not_drop_count_under_contention():
    """并发失败下计数器严格累加（不丢失、不双计）。"""
    b = CircuitBreaker(failure_threshold=100, cooldown_seconds=30)
    barrier = threading.Barrier(30)
    errors = []

    def worker():
        barrier.wait()
        try:
            b.on_failure()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert b.failure_count == 30, f"30 次并发失败应全部入账：实际 {b.failure_count}"
    assert b._total_failures == 30