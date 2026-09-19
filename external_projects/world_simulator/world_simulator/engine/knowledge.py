"""world_simulator/engine/knowledge.py — 跨模拟知识库的安全包装。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

from pathlib import Path

from world_simulator import knowledge_base


def _safe_suggest_knowledge(data_dir: Path, query_text: str, *, template: str) -> str:
    """`knowledge_base.suggest_for_prompt()` 的安全包装（阶段二十，4.12
    节 3.）：检索是"锦上添花"的旁路信息，任何异常（比如知识库文件被
    手工改坏）都不应该让 `generate_scenario`/`advance()` 的核心链路
    失败，退化为"没有可参考的知识"即可，不向上抛出。
    """
    try:
        return knowledge_base.suggest_for_prompt(data_dir, query_text, template=template)
    except Exception:
        return "（暂无相关的已知因果知识）"


def _safe_record_causal_links(
    data_dir: Path, *, sim_id: str, template: str, causal_links: list
) -> None:
    """`knowledge_base.record_causal_links()` 的安全包装（阶段二十，
    4.12 节 2.）：写入知识库是这一步推进落盘*之后*的旁路操作，失败
    不应该让本次推进本身失败（`advance()` 的返回值/落盘结果已经产生），
    这里吞掉异常，只保留"尽力而为"的语义。
    """
    try:
        knowledge_base.record_causal_links(
            data_dir, sim_id=sim_id, template=template, causal_links=causal_links
        )
    except Exception:
        pass
