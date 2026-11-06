import time
from mini_agent.llm.client_pool import ApiKeyPool
from mini_agent.llm.base import LLMRateLimitError

def test_switch_starts_after_failed_key_not_shared_pointer():
    pool = ApiKeyPool(["k1", "k2", "k3", "k4"], rotation="passive",
                      switch_on={"LLMRateLimitError"}, cooldown=60)
    pool._idx = 0                              # 共享指针停在 k1
    nxt = pool.on_error("k3", LLMRateLimitError("limited"))   # 失败的是 k3
    assert nxt == "k4"                         # 从 k3 往后，而不是从 k1 往后得到 k2
    assert pool.snapshot()[2]["cooldown_remaining"] > 0       # k3 已冷却
    assert pool.snapshot()[1]["available"] is True             # k2 没被误冷却
