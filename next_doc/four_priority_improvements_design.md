# 四项优先改进：设计方案（v2，已按反馈确认细节）

> 状态：**设计稿，未开始任何代码修改**，需要你最终确认后再动手。
> 对应上一轮分析里排出的优先级，加上你追加的"记忆巩固（归纳而非淘汰）"，共四项，
> 四项**全部要做**：
> 1. 记忆语义检索（混合 TF-IDF + **本地离线小型 embedding 模型**，默认关闭）
> 2. 记忆巩固：从"淘汰"变成"归纳"（分组阈值默认3，可配置）
> 3. 自主探索：好奇心评分 + 探索结果回写记忆（预算占比默认按现有值，可配置）
> 4. Affordance / 自我模型闭环学习
>
> 文档风格与 `next_doc/priority_improvements_implementation_plan.md` 保持一致：
> 每项都定位到具体文件/函数，说明与现有基础设施的复用关系，不悬空设计。
>
> **本版相对 v1 的变更**：方案一改为本地离线 embedding（不再考虑云端 API），
> 补充了具体的候选模型选型和"开关关闭时零依赖"的实现方式；方案二/三的可
> 配置项做了明确标注；方案四确认要做，不再作为"可选延后项"。

---

## 总体依赖关系

```
① 语义检索后端（基础设施）
    │
    ├──→ ② 记忆巩固（复用①做近重复检测/聚类）
    │
    └──→ ③ 探索好奇心评分（复用①做"新颖度"打分）

④ Affordance/自我模型闭环学习 —— 相对独立，不依赖①②③
```

**建议实施顺序：① → ② → ③ → ④**（④也可与①②③并行，风险面不同，放最后是为了先验证前三项的改动节奏）。

---

## 方案一：记忆语义检索（混合 TF-IDF + 本地离线 Embedding）

> **已按反馈确认**：embedding 必须是**本地离线小模型**（不调用任何云端 API），
> 要小到能在手机上跑（呼应项目里已有的 Android companion app）；必须有独立
> 开关，**关闭时完全不引入新的推理依赖**，不改变任何现有行为。

### 1.1 问题现状

`memory_store.py::_score_all()` / `rank_subset()` 只有 TF-IDF + 中文 n-gram 分词。这套方案对**字面重合**的查询效果不错（"数据库连接失败"能召回含"数据库""连接"的条目），但完全无法处理**语义相近、字面不同**的情况（"接口超时"召回不到"API 调用挂起"）。

代码里已经两处明确留了这个扩展点：
- `memory_base.py` 文档注释："ChromaMemoryBackend — 向量检索，需要 chromadb"（预留但从未实现）
- `lesson_review.py::group_lessons()` 文档注释："不是设计文档 6.4 节描述的完整语义聚类……聚类精度留给后续迭代提升（例如换成 embedding 相似度）"

也就是说，接入语义检索不止改善 `MemoryStore.search()` 本身，还能顺带提升 `group_lessons()` 的聚类质量（这对方案三的 outcome_tracker 判定准确性、方案二的近重复检测都有连带收益）。

### 1.2 候选模型：小到能在手机上跑的本地 embedding 模型

调研了当前（2026年）主流的小型开源 embedding 模型，按"能否在手机上跑"筛选，
候选如下（按推荐优先级排序）：

| 模型 | 参数量 | 量化后体积 | 语言 | 说明 |
|---|---|---|---|---|
| **BAAI/bge-small-zh-v1.5**（推荐默认） | ~95M | INT8 量化约 50-60MB | 中文为主 | 项目记忆内容以中文为主（lesson trigger/summary 多为中文），中文专精模型的检索质量优于通用多语言小模型；已有官方 ONNX 导出，`optimum-cli onnxruntime quantize` 一条命令量化 |
| **Google EmbeddingGemma-300M** | 300M | INT4 量化约 150MB（QAT，非后量化） | 100+ 语言 | 官方明确面向手机/笔记本设计，支持 Matryoshka 降维（768→128 维，可用更小维度进一步省内存/存储），生态支持好（llama.cpp/Ollama/LiteRT），**多语言场景**或未来要支持中英混合项目时的备选 |
| **sentence-transformers/all-MiniLM-L6-v2** | 22M | INT8 量化约 25MB | 英文为主 | 体积最小、速度最快，但中文效果差，不适合本项目做默认，可作为"纯英文项目"场景下的可选项 |
| **intfloat/multilingual-e5-small** | 118M | INT8 量化约 120MB | 多语言 | 效果均衡但不如前两者在各自擅长语言上的表现突出，作为兜底通用选项 |

**默认选择：`BAAI/bge-small-zh-v1.5` 的 INT8 量化 ONNX 版本**，理由：
- 本项目记忆内容（lesson/summary/trigger 文本）以中文场景为主，中文专精模型在实际检索质量上收益最大，优先解决"接口超时 vs API 调用挂起"这类中文语义召回问题。
- 50-60MB 的体积和手机端推理速度（ONNX Runtime Mobile 上单条文本编码通常在几十毫秒级）完全满足"手机上也能跑"的要求，且比 EmbeddingGemma 更小。
- 支持通过配置切换到 EmbeddingGemma-300M（多语言场景）或用户自定义模型路径，不锁死在单一模型上。

**推理运行时：`onnxruntime`**（而非 `sentence-transformers` 完整库）：
- `onnxruntime` 本身有对应的移动端发行版（`onnxruntime-mobile` / Android 端有官方 AAR 包），与项目现有 `android_companion_app/` 的技术路线兼容，未来若要把语义检索能力搬到 Android 端，是同一套推理引擎，不需要换技术栈。
- 只需要 ONNX Runtime + 分词器（`tokenizers` 库或纯 Python 实现的轻量 WordPiece/BPE 分词），不需要引入完整的 PyTorch/`sentence-transformers` 依赖链（这两个库体积以 GB 计，与项目"轻量"定位冲突）。
- **这两个库（`onnxruntime` + `tokenizers`）都作为 extras 依赖**（`pyproject.toml` 里新增 `[project.optional-dependencies] embedding = ["onnxruntime", "tokenizers"]`），装 `pip install mini-agent[embedding]` 才会安装，默认 `pip install mini-agent` 完全不受影响。

模型文件本身（ONNX 权重，几十 MB）不随包分发，改为运行时按需下载（首次启用 embedding 开关时从 Hugging Face 下载到 `~/.agent/models/` 缓存目录，之后离线复用，与 Ollama/llama.cpp 拉模型的体验一致），避免 pip 包本身体积暴涨。

### 1.3 开关设计：关闭时零依赖、零改动

```python
# config/models.py::MemoryConfig 新增字段
backend: str = "local"    # "local" | "hybrid" | "chroma" | "redis"（hybrid 为新增）

embedding_enabled: bool = False   # [默认关闭] 唯一总开关。为 False 时：
                                    #   - 不 import onnxruntime/tokenizers（哪怕已安装）
                                    #   - 不下载/加载任何模型文件
                                    #   - HybridMemoryBackend 等价于纯 MemoryStore
embedding_model: str = "bge-small-zh-v1.5"   # 内置候选名，或用户自定义模型的本地路径
embedding_model_cache_dir: Optional[Path] = None  # None = ~/.agent/models/
embedding_tfidf_weight: float = 0.5
embedding_weight: float = 0.5
embedding_top_n: int = 20
```

关键实现要求：`embedding_enabled=False` 时，`memory_factory.py` 里创建 backend 的代码路径**不能出现任何 `import onnxruntime` 语句被执行**（哪怕在 `try/except ImportError` 里也不行——因为哪怕 import 失败也有一点点开销，更重要的是"用户没装这个包也不该在日志里看到任何相关痕迹"）。做法是把 embedding 相关的 import 全部**延迟到 `embedding_enabled=True` 分支内部**，而不是模块顶层：

```python
# perception/local_embedding.py（新文件，只在 embedding_enabled=True 时被 import）

"""
perception/local_embedding.py — 本地离线 embedding 推理

只有 MemoryConfig.embedding_enabled=True 时，memory_factory.py 才会
import 本模块；本模块内部再对 onnxruntime/tokenizers 做 import，
双重延迟保证"关闭开关=零依赖引入"。
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional

_BUILTIN_MODELS = {
    "bge-small-zh-v1.5": {
        "repo_id": "BAAI/bge-small-zh-v1.5",
        "onnx_file": "onnx/model_quantized.onnx",   # INT8 量化版本
        "dim": 512,
    },
    "embedding-gemma-300m": {
        "repo_id": "google/embeddinggemma-300m",
        "onnx_file": "onnx/model_int4.onnx",
        "dim": 768,   # 支持 MRL 截断到 128/256/512
    },
    # 用户也可以传入本地路径而非内置 key，见 _resolve_model_source()
}


class LocalEmbeddingModel:
    """
    包装 ONNX Runtime 推理 + 分词，提供 embed(text: str) -> list[float]。

    首次调用时：
      1. 检查 cache_dir 下模型文件是否存在，不存在则从 Hugging Face 下载
         （下载失败/无网络：抛出异常，由调用方 HybridMemoryBackend 捕获后
         整体降级为纯 TF-IDF，不影响记忆检索可用性）。
      2. 用 onnxruntime.InferenceSession 加载模型（CPU provider，量化模型
         不需要 GPU）。
      3. 用 tokenizers 库加载对应分词器配置。

    线程/进程安全：InferenceSession 本身线程安全，多个 SessionAgent 实例
    可共享同一个 LocalEmbeddingModel 单例（避免每个 session 重复加载模型
    占用内存——量化模型虽小但没必要每 session 加载一份）。
    """

    def __init__(self, model_name: str, cache_dir: Optional[Path] = None):
        self._model_name = model_name
        self._cache_dir = cache_dir or (Path.home() / ".agent" / "models")
        self._session = None   # 懒加载
        self._tokenizer = None

    def embed(self, text: str) -> list[float]:
        self._ensure_loaded()
        # tokenize -> onnxruntime session.run -> mean pooling -> L2 normalize
        ...

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort   # 延迟 import，见模块文档
        from tokenizers import Tokenizer
        ...


_instance_cache: dict[str, "LocalEmbeddingModel"] = {}   # 进程内单例缓存，按 model_name 复用


def get_shared_embedding_model(model_name: str, cache_dir: Optional[Path] = None) -> "LocalEmbeddingModel":
    """进程内单例：多个 SessionAgent/HybridMemoryBackend 共享同一份加载好的模型。"""
    key = f"{model_name}:{cache_dir}"
    if key not in _instance_cache:
        _instance_cache[key] = LocalEmbeddingModel(model_name, cache_dir)
    return _instance_cache[key]
```

`memory_factory.py::_load_local()` 里的改造（关闭时行为完全不变）：

```python
def _load_local(cfg, scope="project", user_id=None) -> MemoryBackend:
    ... # 现有 MemoryStore 构造逻辑完全不变

    if not getattr(cfg.memory, "embedding_enabled", False):
        return store   # [默认路径] 未开启 embedding，直接返回原有 MemoryStore，零改动

    try:
        from mini_agent.perception.hybrid_memory_backend import HybridMemoryBackend
        from mini_agent.perception.local_embedding import get_shared_embedding_model
        embed_model = get_shared_embedding_model(cfg.memory.embedding_model, cfg.memory.embedding_model_cache_dir)
        return HybridMemoryBackend(
            inner=store,
            embed_call=embed_model.embed,
            tfidf_weight=cfg.memory.embedding_tfidf_weight,
            embedding_weight=cfg.memory.embedding_weight,
        )
    except Exception:
        # 模型下载失败/onnxruntime 未安装（用户开了开关但没装 extras）/加载出错：
        # 静默降级为纯 MemoryStore，不阻断 agent 启动，只在 debug 日志里记录原因
        import logging
        logging.getLogger(__name__).warning(
            "[embedding] 加载本地 embedding 模型失败，已降级为纯 TF-IDF 检索。"
            "如果你已开启 embedding_enabled，请确认已安装 `pip install mini-agent[embedding]`。"
        )
        return store
```

这样"开关开着但没装 extras 依赖"这种误操作也有清晰的降级路径和提示，不会直接报错崩溃。

### 1.4 移动端的进一步意义

因为选用 `onnxruntime`（有官方 Android AAR）而非 PyTorch 生态，这套 embedding 推理逻辑未来理论上可以直接移植到 `android_companion_app/`（项目已有的 Android 伴侣应用）里本地跑，实现"手机端也能做语义记忆检索"而不必依赖主机 daemon——这个不在本次改动范围内，但选型时已经把这条路留出来了，不需要将来推倒重来。

### 1.5 设计方案：新增 `HybridMemoryBackend`，不改造 `MemoryStore`

**关键决策：不修改现有 `MemoryStore`，而是新增一个包装类**，通过 `memory_factory.py` 已有的 `register_memory_backend()` 扩展点注册为新的 `backend` 选项（`cfg.memory.backend = "hybrid"`）。理由：
- `MemoryStore` 是当前默认路径，大量测试和行为依赖它的精确输出（`tests/test_memory_aging.py` 等），直接改造风险大。
- `memory_base.py::MemoryBackend` 抽象接口本来就是为"多后端并存"设计的，`_REGISTRY` 机制已经就绪，新增而非替换是这套架构的本意用法。
- 用户可以先在小范围/新项目里试用 `hybrid` 后端，默认值仍是 `local`，零风险切换。

```python
# src/mini_agent/perception/hybrid_memory_backend.py（新文件）

"""
perception/hybrid_memory_backend.py — 混合检索记忆后端

包装 MemoryStore（TF-IDF + n-gram，精确匹配兜底），新增 embedding 语义召回：
  - add()/search_by_tag()/delete_by_session()/reload() 等全部委托给内部 MemoryStore，
    行为完全不变。
  - search() 改为：TF-IDF 召回 top-N + embedding 语义召回 top-N，
    按可配置权重（tfidf_weight / embedding_weight）合并去重排序。

embedding 来源：perception/local_embedding.py::get_shared_embedding_model()
返回的本地 ONNX 模型 embed() 方法（见 1.2/1.3 节），不依赖任何云端 provider。

失败降级：embedding 调用失败/模型未加载成功时，search() 自动
退化为纯 TF-IDF（与 MemoryStore.search() 结果完全一致），不阻断记忆检索。

embedding 向量的持久化：不引入外部向量数据库依赖（保持"无外部依赖"的项目
定位），改为在 MemoryEntry 旁维护一个 <path>.embeddings.jsonl 影子文件
（entry_id -> 向量），首次访问时懒加载并为缺失向量的旧条目补算。
"""
```

**核心接口**：

```python
class HybridMemoryBackend(MemoryBackend):
    def __init__(
        self,
        inner: "MemoryStore",
        embed_call: Optional[Callable[[str], list[float]]] = None,
        tfidf_weight: float = 0.5,
        embedding_weight: float = 0.5,
        embedding_top_n: int = 20,   # 语义召回的候选池大小（再与 TF-IDF 合并排序）
    ) -> None:
        self._inner = inner
        self._embed_call = embed_call
        self._vectors_path = inner._path.with_suffix(".embeddings.jsonl")
        self._vectors: dict[str, list[float]] = {}   # entry_id -> vector，懒加载

    # 委托：与 MemoryStore 行为完全一致
    def add(self, entry): 
        self._inner.add(entry)
        self._maybe_embed_async(entry)   # 不阻塞写入路径，向量计算失败不影响 add() 成功
    def search_by_tag(self, tag): return self._inner.search_by_tag(tag)
    def delete_by_session(self, sid): 
        self._inner.delete_by_session(sid)
        self._vectors.pop(sid, None)   # 简化示意，实际按 entry_id 清理
    def reload(self): self._inner.reload()
    @property
    def count(self): return self._inner.count
    def all_entries(self): return self._inner.all_entries()

    # 核心改动
    def search(self, query: str, k: int = 3) -> list["MemoryEntry"]:
        tfidf_ranked = self._inner._score_all(query)   # 复用现有私有方法，取全量分数而非直接截断
        if self._embed_call is None:
            return [e for e, s in sorted(tfidf_ranked, key=lambda x: -x[1])[:k] if s > 0]

        query_vec = self._safe_embed(query)
        if query_vec is None:
            return [e for e, s in sorted(tfidf_ranked, key=lambda x: -x[1])[:k] if s > 0]

        embed_ranked = self._embedding_score_all(query_vec)  # cosine similarity
        merged = self._merge_scores(tfidf_ranked, embed_ranked)
        return [e for e, s in sorted(merged, key=lambda x: -x[1])[:k] if s > 0]
```

**合并策略**：两路分数各自做 min-max 归一化后按权重相加（避免 TF-IDF 的 IDF 量纲和 cosine similarity 的 [-1,1] 量纲直接相加失真）。默认权重 5:5，可配置——纯字面匹配场景（比如报错信息里的具体路径/变量名）TF-IDF 往往更准，纯概念性查询 embedding 更准，5:5 是稳妥的起点，不建议一开始就调权重，先跑一段时间观察 `/evolution outcomes` 里检索质量是否有连带改善再调。

> 配置字段已在 1.3 节列出，不再重复。

### 1.7 与 `group_lessons()` 的连带改进（可选，作为方案一的子任务）

`lesson_review.py::group_lessons()` 目前用 trigger 文本关键词 Jaccard 相似度聚类。当 `hybrid` 后端可用时，可以给 `group_lessons()` 增加一个可选参数 `embed_call`，聚类判定从"关键词 Jaccard ≥ 阈值"改为"关键词 Jaccard ≥ 阈值 **或** embedding cosine similarity ≥ 阈值"（两路取并集，因为语义聚类的假阳性比假阴性危害更大——错误合并两个不相关 lesson 比漏合并更糟，所以保留关键词路径作为兜底而非替换）。这个子任务依赖方案一但不阻塞方案一本身落地，可以拆成独立的小 PR。

### 1.8 测试与验收标准

- 新增 `tests/test_hybrid_memory_backend.py`：
  - `embed_call=None` 时 `search()` 结果与纯 `MemoryStore.search()` 逐条一致（回归保证）。
  - mock embedding 调用，验证语义召回能找到 TF-IDF 召回不到的条目（构造"接口超时" query / "API 调用挂起"条目的用例）。
  - embedding 调用抛异常时自动降级，不影响返回结果。
- 验收标准：不要求"检索质量指标"这类难以量化的东西作为合并门槛，只要求"回归不变 + 语义召回能力已验证存在"。真实检索质量提升与否，交给方案三/四的效果回填机制间接观察。

### 1.9 风险与不做的事

- **不做**：不引入 chromadb/faiss 等外部向量库依赖（保持项目"无外部依赖"的定位），向量检索用简单的 numpy 余弦相似度线性扫描即可——记忆条目上限本来就是 `max_entries=500`，线性扫描 500 条向量的开销可忽略，不需要近似最近邻索引。
- **不做**：不改变 `MemoryEntry` 数据结构（向量存在影子文件里，不侵入 `entry_id`/JSONL 主文件格式），避免和现有 `library_index`/`memory_aging` 等大量依赖 `MemoryEntry` 字段结构的模块产生耦合。

---

## 方案二：记忆巩固——从"淘汰"变成"归纳"

> **已按反馈确认**：分组阈值先用 `MIN_CONSOLIDATE_GROUP_SIZE = 3`，做成配置项
> （`consolidation_min_group_size`，见 2.5 节），后续可直接改配置调整，不需要改代码。

### 2.1 问题现状

`MemoryStore.add()` 里，超过 `max_entries`（默认500）时：
```python
self._entries.sort(key=lambda e: e.created_at)
self._entries = self._entries[-self._max_entries:]
self._rewrite_disk()
```
纯粹按时间淘汰最旧的，没有任何"信息保留"的考虑。而 `phase_g.py` 里已有的"8.6 知识巩固"（`library.consolidate()`）巩固的是**分类树节点**和**实体摘要**（`entity_index.py`），巩固对象是"图书馆式索引"里的结构化元数据，**不覆盖**原始的 `MemoryEntry` 池本身——一条旧的 lesson 条目哪怕被 `library.consolidate()` 归好类、贴好标签，只要它排到 `max_entries` 之外，还是会被 `add()` 直接物理删除，之前所有的分类/归纳成果一起被扔掉。

人类记忆巩固的本质是"多条具体经历 → 一条抽象规律"，现有系统完全没有这一层，只有物理淘汰。

### 2.2 设计方案：淘汰前插入"归纳"步骤

**核心思路**：不改变淘汰机制本身（依然需要控制条目数量上限），而是在**淘汰发生之前**，对"即将被淘汰的一批旧条目"做一次尝试性归纳——如果归纳成功，用一条新的、更抽象的 `entry_type="consolidated_lesson"` 条目替换掉这一批，而不是直接销毁；归纳失败（不够相似、没有 LLM 可用等）则退回原有的直接淘汰行为。

**新增模块** `evolution/memory_consolidation.py`：

```python
"""
evolution/memory_consolidation.py — 记忆巩固：归纳而非纯淘汰

对应用户反馈的缺口：MemoryStore 淘汰旧条目时是纯粹的"最旧优先删除"，
没有像人类记忆巩固那样"多条具体经历 -> 一条抽象规律"的归纳过程。

设计原则：
  - 不替换现有淘汰机制，只在淘汰发生前插入一步"尝试归纳"；归纳失败时
    完全退化为原有行为（物理删除），保证这是纯增量、可关闭的改动。
  - 只对 entry_type == "lesson" 的条目做归纳（summary 型条目是"这次
    session 发生了什么"的记录，归纳会丢失时间线信息，价值不同，不适用
    同样的巩固逻辑，继续走原有淘汰）。
  - 复用 lesson_review.py::group_lessons() 的聚类能力，不重新实现一套
    相似度判断。
  - 复用方案一的 embedding（若可用）辅助判断"是否值得合并"，不可用时
    退化为纯关键词 Jaccard（与 group_lessons 现有行为一致）。
  - 归纳产物 occurrence_count 累加原有条目之和，confidence 取加权平均，
    半衰期基准沿用 memory_aging.py 里最高优先级来源（human_feedback 优先
    于 self_reflection），避免"归纳后反而降低了重要经验的记忆强度"。
  - 归纳是有损压缩：原始条目的具体 trigger 文本会被合并摘要覆盖。因此
    默认要求聚类规模 >= MIN_CONSOLIDATE_GROUP_SIZE（默认3）才触发归纳，
    避免"仅两条偶然相似的经历"被过度抽象成误导性规则。
"""

MIN_CONSOLIDATE_GROUP_SIZE = 3   # 至少3条才归纳，避免小样本过度抽象
CONSOLIDATE_TRIGGER_RATIO = 0.9  # 淘汰候选（超出 max_entries 的最旧部分）
                                  # 达到这个比例时才触发归纳扫描，避免每次
                                  # add() 都做一次全量聚类分析
```

**核心函数**：

```python
def consolidate_before_eviction(
    entries_to_evict: list["MemoryEntry"],
    *,
    embed_call: Optional[Callable] = None,
    llm_call: Optional[Callable[[str], str]] = None,
) -> tuple[list["MemoryEntry"], list["MemoryEntry"]]:
    """
    输入：即将被淘汰的旧条目列表（MemoryStore 按 created_at 排序后超出
    max_entries 的那一批）。

    返回：(consolidated_entries, truly_evicted_entries)
      consolidated_entries — 归纳产生的新条目（entry_type="consolidated_lesson"），
                              应该被保留写入（替代原有的一批旧条目）
      truly_evicted_entries — 未能归纳、按原逻辑物理删除的条目

    流程：
      1. 只挑 entry_type == "lesson" 的条目参与归纳，其余（summary 等）
         直接进 truly_evicted_entries（行为不变）。
      2. 复用 lesson_review.group_lessons() 对候选做聚类
         （若传入 embed_call，聚类判定同时参考语义相似度，见方案一 1.4节）。
      3. 对聚类规模 >= MIN_CONSOLIDATE_GROUP_SIZE 的分组：
         - 若提供 llm_call：生成一条抽象化摘要（"这一类场景反复出现的
           规律是……"），写成新的 MemoryEntry：
             entry_type="consolidated_lesson"
             trigger = 该聚类的共性触发场景描述（LLM 生成）
             suggested_action = 归纳后的通用建议
             occurrence_count = sum(原条目 occurrence_count)
             confidence = max(原条目 confidence)  # 保守取最高置信度而非平均，
                                                    # 避免归纳时被低置信度条目拉低
             source = "consolidated"   # 新增来源类型，见 2.3 节半衰期处理
         - 若无 llm_call：退化为规则拼接（取聚类里 occurrence_count 最高
           的一条作为代表，其余条目的 occurrence_count 累加到它身上），
           不生成新的抽象摘要文本，只做"多条计数合一"，仍然优于纯粹丢弃。
      4. 聚类规模 < MIN_CONSOLIDATE_GROUP_SIZE 或分组失败：原样进入
         truly_evicted_entries（回退到现有行为）。
      5. 全程失败静默降级：任何异常直接返回
         ([], entries_to_evict)，等价于完全跳过归纳步骤。
    """
```

**接入点**：`memory_store.py::MemoryStore.add()`

```python
def add(self, entry: MemoryEntry) -> None:
    self._ensure_loaded()
    # ... 现有 library.on_new_entry 逻辑不变 ...
    self._entries.append(entry)
    if len(self._entries) > self._max_entries:
        self._entries.sort(key=lambda e: e.created_at)
        evict_count = len(self._entries) - self._max_entries
        candidates = self._entries[:evict_count]
        keep = self._entries[evict_count:]

        # [记忆巩固] 尝试归纳而非直接丢弃，见 evolution/memory_consolidation.py
        if self._consolidation_enabled:
            try:
                from mini_agent.evolution.memory_consolidation import consolidate_before_eviction
                consolidated, truly_evicted = consolidate_before_eviction(
                    candidates,
                    embed_call=self._embed_call,   # None 时函数内部自动降级
                    llm_call=self._llm_classify_call,  # 复用已有的兜底分类 LLM 调用
                )
                self._entries = consolidated + keep
            except Exception:
                self._entries = keep  # 归纳失败，退回原有行为
        else:
            self._entries = keep

        self._rewrite_disk()
    else:
        self._append_to_disk(entry)
```

关键点：**归纳产物本身也占用 `max_entries` 里的一个位置**（N 条旧记忆 → 1 条归纳记忆，是净减少，不会破坏"控制存储上限"这个原有目标），所以这个改动不需要放大 `max_entries`，是纯粹的"淘汰逻辑升级"而不是"新增存储层"。

### 2.3 半衰期处理

`memory_aging.py::compute_decay_factor()` 需要新增对 `source == "consolidated"` 的分支——归纳产物代表"反复验证过的规律"，理应比单次 `self_reflection` 衰减更慢，但又不应该凭空高于 `human_feedback`（毕竟归纳是自动化推断，不是人类明确确认）。建议半衰期基准：`consolidated` = 45 天（介于 self_reflection 30 天与 human_feedback 90 天之间），并保留 `occurrence_count` 加成（封顶 4 倍）逻辑不变。

### 2.4 与 `library_index` 巩固的关系

明确定位：`library.consolidate()`（8.6 知识巩固）巩固的是**分类树/实体索引**这层元数据结构，本方案的 `consolidate_before_eviction()` 巩固的是 **`MemoryEntry` 原始记忆池**本身，两者作用对象不同、互不冲突。归纳产生的新条目 (`consolidated_lesson`) 仍然会正常走 `library.on_new_entry()` 归类流程（`MemoryStore.add()` 里现有逻辑不变），所以两层巩固机制是叠加而非替代关系。

### 2.5 配置扩展

```python
# config/models.py::MemoryConfig 新增字段
consolidation_enabled: bool = True   # 淘汰前是否尝试归纳（默认开，失败静默降级不影响可用性）
consolidation_min_group_size: int = 3
```

### 2.6 测试与验收标准

- 新增 `tests/test_memory_consolidation.py`：
  - 构造 5 条相似 lesson + 触发淘汰，验证归纳后条目数减少但 `occurrence_count` 总和不丢失。
  - `llm_call=None` 时验证走"规则拼接"降级路径而非直接失败。
  - 聚类规模不足 `MIN_CONSOLIDATE_GROUP_SIZE` 时验证走原有物理淘汰路径（回归不变）。
  - `consolidation_enabled=False` 时验证 `MemoryStore.add()` 行为与改造前逐字节一致。

---

## 方案三：自主探索——好奇心评分 + 探索结果回写记忆

> **已按反馈确认**：探索预算先按 `ExplorationSandbox` 现有的
> `exploration_budget_ratio` 不变（好奇心候选和确定性问题候选公用同一份预算，
> 只是排序权重会考虑 novelty），`novelty_weight`/`exploration_min_calls_threshold`/
> `already_explored_cooldown_days` 均做成配置项（见 3.3 节），后续可直接调配置。

### 3.1 问题现状

`soft_goal_deriver.py::_from_capability_map()` 的探索候选只来自"已知的低置信度能力"（`confidence < CONFIDENCE_LOW` 且 `total_calls >= 3`），本质是"针对已经暴露出问题的领域做复习"，不是主动发现未知。`_DeriveCandidate.urgency` 字段目前的打分公式（`(CONFIDENCE_LOW - confidence) * 10 + total_calls * 0.1`）只反映"这件事有多紧急/有多少证据"，不反映"探索这件事能学到多少新东西"。

同时，`exploration_sandbox.py` 的 `ExplorationReport`（探索结果）目前的终点是"成功的提升为正式 skill 提案"，中间过程中产生的信息（包括**失败的探索**）没有看到写回 lesson memory 的路径——这意味着同样的探索性错误可能被重复"发现"。

### 3.2 设计方案

#### 3.2.1 新增探索候选来源：`_from_unexplored_capabilities()`

在 `_from_capability_map()` 旁新增一路信号，专门捕捉"完全没有数据、而非有数据但置信度低"的领域——这是"未知的未知"与"已知的不足"的区别：

```python
def _from_unexplored_capabilities(self) -> list[_DeriveCandidate]:
    """
    信号 4（新增）：capability_map 里 total_calls 极少（< MIN_CALLS_FOR_KNOWN，
    默认2）的能力条目，或者 skill 目录里存在但 tracker 记录从未被调用过的
    技能（复用 phase_g.py::_days_since_last_use() 同款 tracker 基础设施，
    但反过来找"从未使用"而非"长期未用"）。

    与 _from_capability_map() 的区别：
      _from_capability_map — "试过，效果不好" → urgency 来自"确定性的失败信号"
      _from_unexplored_capabilities — "几乎没试过" → urgency 来自"信息增益"，
        即"探索这个领域能在多大程度上减少 agent 对自己能力的不确定性"
    """
```

#### 3.2.2 好奇心评分：用"不确定性"而非"确定性失败"排序

新增打分维度，与现有 `urgency` 并列而非替换（避免破坏 `_from_capability_map()`/`_from_work_index()`/`_from_lesson_review()` 现有排序语义）：

```python
@dataclass
class _DeriveCandidate:
    title: str
    description: str
    source_tag: str
    priority: int = 20
    urgency: float = 0.0       # 既有字段：紧急度/确定性问题的严重度
    novelty: float = 0.0       # [新增] 好奇心/信息增益评分，默认0（旧三路信号不产出，行为不变）
```

好奇心评分公式（`_from_unexplored_capabilities()` 内部）：
```
novelty = 1.0 / (1 + total_calls)          # total_calls 越少，novelty 越高（信息增益越大）
        * recency_bonus                    # 若该 capability 关联的技能/工具是近期新增的，加成
        * (1 - already_explored_penalty)   # 若最近30天内已经被探索过（复用 exploration_sandbox
                                            # 的 ExplorationReport 历史记录判断），大幅降权避免重复探索
```

`derive_candidates()` 的排序逻辑需要相应调整——目前是纯按 `urgency` 降序，改为按 `urgency + novelty_weight * novelty` 排序（`novelty_weight` 可配置，默认 0.5，让"确定性问题"和"好奇心驱动的探索"按可调权重竞争有限的探索预算，而不是好奇心永远排在后面）：

```python
# derive_candidates() 内排序行由：
for c in sorted(all_candidates, key=lambda x: x.urgency, reverse=True):
# 改为：
novelty_weight = getattr(self._cfg.autonomy, "novelty_weight", 0.5) if hasattr(self._cfg, "autonomy") else 0.5
for c in sorted(all_candidates, key=lambda x: x.urgency + novelty_weight * x.novelty, reverse=True):
```

#### 3.2.3 探索结果回写记忆（无论成功失败）

`exploration_sandbox.py` 的 `ExplorationReport` 目前只在成功时流向 skill 提案。新增一步：**探索结束后无条件生成一条 lesson memory**（成功/失败都写，`source="exploration"`）：

```python
# ExplorationSandbox 完成一次探索试验后（无论 verdict 是否成功）：
def _record_exploration_outcome(self, report: "ExplorationReport", memory_backend) -> None:
    """
    探索无论成功失败都应该沉淀为经验，否则同样的探索性错误会被重复"发现"，
    浪费探索预算。

    - 成功：outcome="验证有效，已提升为 skill 提案候选"，confidence 较高
    - 失败：outcome="尝试 X 方式不可行，原因：Y"，confidence 中等
      （这类"此路不通"的负面经验同样有价值——防止未来的 SoftGoalDeriver
      或 skill_propose 再次把同一条路径列为候选）

    entry_type="lesson", source="exploration"，写入 memory_backend（若可用），
    半衰期基准可与 self_reflection 相同（30天）——探索结论不如人类反馈可靠，
    但也不应该衰减过快导致刚探索过的"此路不通"很快被遗忘又重新尝试。
    """
```

`_from_unexplored_capabilities()` 里的 `already_explored_penalty` 正是通过 `search_by_tag("exploration")` 或按 `source="exploration"` 过滤 lesson memory 来实现"最近探索过的不重复探索"，这样"探索结果回写记忆"和"好奇心评分"两条改动形成一个真正的闭环：探索 → 写记忆 → 下次评分时降权已探索领域 → 好奇心自然流向真正未知的领域。

### 3.3 配置扩展

```python
# config/models.py 新增（挂在合适的 autonomy/exploration 相关 config 上）
novelty_weight: float = 0.5
exploration_min_calls_threshold: int = 2   # total_calls 低于此值视为"几乎未探索"
already_explored_cooldown_days: float = 30.0
```

### 3.4 测试与验收标准

- 新增 `tests/test_curiosity_scoring.py`：
  - 验证 `total_calls=0` 的能力条目 novelty 分数高于 `total_calls=10, confidence=0.3` 的条目（后者走 urgency 路径而非 novelty）。
  - 验证最近探索过的领域 novelty 被正确降权。
  - 验证 `_from_capability_map()`/`_from_work_index()`/`_from_lesson_review()` 三路现有信号的 `novelty` 默认值为0，排序结果在 `novelty_weight=0` 时与改造前完全一致（回归保证）。
- 新增 `tests/test_exploration_outcome_recording.py`：
  - 验证探索失败时也生成 lesson memory 条目（而不是只有成功才写）。
  - 验证 `already_explored_penalty` 正确读取到最近的探索记录。

---

## 方案四：Affordance / 自我模型闭环学习

> **已按反馈确认：本方案确定要做**，不再作为可选延后项。

### 4.1 问题现状

`affordance_analyzer.py::AffordanceAnalyzer.analyze()` 明确是"无状态、只读，不做任何写入"的纯函数式聚合——`known_issues`/`unexplored_areas`/`high_risk_zones`/`top_opportunities` 全部来自对 `open_threads`/`capability_map`/`lesson_entries` 三份既有数据的重新排序展示，**没有自己的权重参数**，也就没有"学习"这一说：无论 AffordanceMap 展示的建议被采纳后效果如何，`AffordanceAnalyzer` 自身的排序逻辑永远不变。

`self_model.py::AgentSelfModel` 同理，是构建时的一次性快照。

### 4.2 设计方案：给 Affordance 排序引入可学习的置信度权重，由 outcome_tracker 间接校准

**核心思路**：不改变 `AffordanceAnalyzer.analyze()` 的"session 开始时构建一次的慢变量"这一定位（这是 v3 文档里明确讨论过并坚持的设计原则，改成逐 turn 更新会破坏慢/快变量分层，参见 `priority_improvements_implementation_plan.md` 方案二 2.3 节的论证，本方案延续同一原则）。改为**让 `top_opportunities` 的排序权重可以被历史效果校准**，而不是永远用同一套固定规则。

**新增模块** `perception/affordance_calibration.py`：

```python
"""
perception/affordance_calibration.py — Affordance 排序权重的闭环校准

问题：AffordanceAnalyzer.analyze() 对 known_issues/unexplored_areas/
high_risk_zones 三路信号的排序权重是硬编码的（各展示 top 2-3 条，无相对
优先级学习）。如果"AffordanceMap 建议关注 X"之后，X 相关的工作最终被
SoftGoalDeriver derive 成 Goal 并被 outcome_tracker 判定为 improved，
说明这类建议"值得信"；如果多次被用户忽略或 derive 出的 Goal 被 reject，
说明这类建议的信噪比不高，应该降低其展示优先级。

设计原则（与本项目一贯的"感知层只读、不直接改变行为"原则一致）：
  - 不修改 AffordanceAnalyzer.analyze() 的核心聚合逻辑，只新增一个可选的
    weight_overrides 参数，默认值保持现有硬编码行为（三路各自的展示条数
    2-3-3-3 是隐式的相等权重）。
  - 校准数据来源全部复用已有基础设施，不新增数据采集：
      - outcome_tracker.py 的 verdict 记录（improved/worsened）
      - soft_goal_deriver.py 的 rejected keys（用户 reject 的 Goal 记录）
      - 通过 title/description 关键词与 AffordanceMap 三路来源
        （known_issues 来自 open_threads，unexplored_areas 来自
        capability_map，high_risk_zones 来自 lesson）做关联，判断某条
        Affordance 提示最终演变成的 Goal/commit 效果如何。
  - 权重更新是周期性的（挂在 Phase G 周期扫描里，calibrate() 类似
    outcome_tracker.tick() 的调用方式），不是逐 turn 更新，与"慢变量"
    定位一致。
"""

@dataclass
class AffordanceWeights:
    known_issues_weight: float = 1.0
    unexplored_areas_weight: float = 1.0
    high_risk_zones_weight: float = 1.0
    # 学习方式：每次某一路来源关联的 Goal 被判定 improved，对应权重
    # * LEARNING_RATE 上调（封顶 2.0）；被判定 worsened 或被连续 reject，
    # 权重 * (1 - LEARNING_RATE) 下调（下限 0.3，不允许某一路权重降到0——
    # 即使某类建议历史效果不佳，也不应该完全消失，只是降低展示优先级，
    # 保留"万一这次不一样"的探索空间）。


def calibrate(paths: "AgentPaths") -> AffordanceWeights:
    """
    周期性调用（Phase G 里新增一步，类似 outcome_tracker.tick()）：
      1. 读取 outcome_tracking.json 里已 resolved 的记录
      2. 尝试关联到 AffordanceMap 三路来源之一（关键词匹配，容忍关联失败——
         并非所有 commit 都能追溯到某条 Affordance 提示，关联不上的直接跳过）
      3. 按 verdict 调整对应来源权重
      4. 持久化到 <project_root>/.agent/affordance_weights.json
      5. 失败静默降级：任何异常直接返回默认权重（AffordanceWeights()），
         等价于本次校准跳过，不影响 AffordanceAnalyzer 现有行为。
    """
```

**接入 `AffordanceAnalyzer.analyze()`**：

```python
def analyze(
    self,
    *,
    open_threads=None,
    lesson_entries=None,
    capability_entries=None,
    behavior_context=None,
    weights: Optional["AffordanceWeights"] = None,   # [新增] 默认 None = 现有硬编码行为
) -> "AffordanceMap":
    weights = weights or AffordanceWeights()  # 默认权重全部为1.0，等价于改造前行为
    # top_opportunities 的候选合并排序时，各来源分数乘以对应 weights.*_weight
    # 而不再是固定各展示 top N 条
    ...
```

**接入 `inject_affordance_map()`**（`affordance_analyzer.py` 里已有的共享入口函数）：

```python
def inject_affordance_map(agent, cfg, *, log=None) -> None:
    ...
    try:
        from mini_agent.perception.affordance_calibration import load_weights
        weights = load_weights(paths)   # 读取上次 calibrate() 持久化的权重，文件不存在则默认权重
    except Exception:
        weights = None
    affordance_map = AffordanceAnalyzer().analyze(..., weights=weights)
```

**Phase G 接入**：`phase_g.py::run_phase_g()` 新增一步，紧跟在 outcome_tracking 之后（复用同样的失败静默降级模式）：

```python
try:
    from mini_agent.perception.affordance_calibration import calibrate
    report.affordance_weights_updated = calibrate(paths)
except Exception:
    ... # 失败静默降级，与其它步骤一致
```

### 4.3 为什么不做成"实时强化学习"

- 校准信号（outcome_tracker verdict）本身就有 14 天的观察窗口延迟，不存在"实时反馈"的基础，做成 RL 式的即时更新没有意义。
- 关联判断（哪个 commit 对应哪条 Affordance 提示）是启发式关键词匹配，噪声较大，权重调整需要保守的学习率和边界（`LEARNING_RATE` 建议 0.1，权重下限 0.3 上限 2.0），避免个别误关联的样本剧烈扭曲长期排序策略。
- 与项目一贯的"自动化到建议为止，不自动执行决策"哲学一致：这里"决策"指的是排序优先级的缓慢调整，不是自动执行任何操作，风险可控。

### 4.4 测试与验收标准

- 新增 `tests/test_affordance_calibration.py`：
  - 构造已 resolved 的 outcome_tracking 记录，验证权重正确上调/下调。
  - 验证权重不会突破 [0.3, 2.0] 边界。
  - 验证关联失败（找不到对应来源）时不影响其它来源权重。
  - `weights=None` 时 `AffordanceAnalyzer.analyze()` 结果与改造前完全一致（回归保证）。

---

## 汇总：改动文件清单

| 方案 | 新增文件 | 修改文件（改动范围） |
|---|---|---|
| ① 语义检索 | `perception/hybrid_memory_backend.py` | `memory_factory.py`（注册新 backend）、`config/models.py`（新增字段）、`lesson_review.py`（可选，group_lessons 语义聚类子任务） |
| ② 记忆巩固 | `evolution/memory_consolidation.py` | `memory_store.py::add()`、`evolution/memory_aging.py`（新增 consolidated 来源半衰期）、`config/models.py` |
| ③ 好奇心探索 | 无新文件 | `evolution/soft_goal_deriver.py`（新增 `_from_unexplored_capabilities()` + novelty 排序）、`perception/exploration_sandbox.py`（探索结果回写记忆）、`config/models.py` |
| ④ Affordance 闭环 | `perception/affordance_calibration.py` | `perception/affordance_analyzer.py`（新增可选 weights 参数）、`evolution/phase_g.py`（新增 calibrate 调用） |

四项改动共同点：**全部通过新增可选参数/新文件实现，默认值均等价于改造前行为，失败均静默降级**，与项目现有代码里反复出现的"纯增量、可关闭"原则保持一致，不会破坏现有测试。

---

## 确认结果汇总

| 问题 | 确认结果 |
|---|---|
| embedding 来源 | 本地离线小模型，默认 `bge-small-zh-v1.5`（INT8 量化 ONNX，~50-60MB），可配置切换到 `embedding-gemma-300m` 或用户自定义模型；`embedding_enabled` 默认 `False`，关闭时不引入 `onnxruntime`/`tokenizers` 依赖 |
| 方案二归纳粒度 | `consolidation_min_group_size` 默认 3，做成配置项 |
| 方案三探索预算 | 沿用现有 `ExplorationBudget`/`exploration_budget_ratio` 不变，`novelty_weight` 等新增权重做成配置项，默认值先按方案里给出的（0.5 / 2 / 30天），后续可调 |
| 方案四是否做 | 确认要做 |

四项方案设计已定稿，**接下来会按 ① → ② → ③ → ④ 的顺序动手实现**（原因见文档开头"总体依赖关系"：②③复用①的能力）。

每完成一项，我会：
1. 跑一遍项目现有测试，确认没有回归；
2. 补充该项设计里列出的新增测试，更新对应的文档；
3. 简要汇报改动内容，打包所有修改和新增的文件，再进入下一项。

如果没有其它要调整的地方，我现在开始实现**方案一（本地离线 embedding + HybridMemoryBackend）**。
