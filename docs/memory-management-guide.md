# 记忆管理指南

## 概述

mini-agent 的记忆管理系统提供跨 session 的长期记忆能力，支持会话历史的持久化存储和相关记忆检索。系统采用可扩展架构，支持多种后端存储实现。

## 核心架构

### 分层设计

```
┌─────────────────────────────────────────────┐
│           ContextBuilder / Agent            │
│         (使用记忆的调用层)                   │
└─────────────────┬───────────────────────────┘
                  │ 依赖抽象接口
                  ▼
┌─────────────────────────────────────────────┐
│            MemoryBackend (ABC)              │
│         统一接口定义（抽象基类）              │
│  - add(entry)                               │
│  - search(query, k)                         │
│  - search_by_tag(tag)                       │
│  - count (property)                         │
└─────────────────┬───────────────────────────┘
                  │ 工厂创建
                  ▼
┌─────────────────────────────────────────────┐
│         MemoryFactory (create_...)          │
│         根据配置创建后端实例                  │
└─────────────────┬───────────────────────────┘
                  │ 返回具体实现
                  ▼
┌─────────────────────────────────────────────┐
│           MemoryStore (local)               │
│    JSONL 存储 + TF-IDF 检索 + 时间衰减       │
└─────────────────────────────────────────────┘
```

### 组件说明

#### 1. `MemoryBackend` (abstract)

定义记忆系统的统一接口，所有后端实现都必须继承此类。

**位置**: `src/mini_agent/perception/memory_base.py`

**核心方法**:
- `add(entry: MemoryEntry) -> None` - 持久化记忆
- `search(query: str, k: int) -> list[MemoryEntry]` - 关键词检索
- `search_by_tag(tag: str) -> list[MemoryEntry]` - 标签过滤
- `count -> int` - 条目总数

#### 2. `MemoryStore` (local backend)

本地 JSONL 存储实现，使用 TF-IDF + 时间衰减算法进行记忆检索。

**位置**: `src/mini_agent/perception/memory_store.py`

**存储格式**:
```json
{"session_id": "...", "summary": "...", "key_outcomes": [...], "tags": [...], "model": "...", "created_at": 1234567890, "entry_id": "abc123"}
```

**检索算法**:
1. **分词策略**:
   - 英文：按单词切分，过滤停用词
   - 中文：双字 + 三字 n-gram（如"数据库" → ["数据", "据库", "数据库"]）

2. **TF-IDF 评分**:
   - `TF = 词频 / 总词数`
   - `IDF = log((N + 1) / (df + 1)) + 1`
   - 综合分数 = TF × IDF

3. **时间衰减**:
   - 衰减系数 λ = ln(2) / 30（半衰期 30 天）
   - 最终分数 = TF-IDF × exp(-λ × age_days)

4. **容量管理**:
   - 默认最多 500 条记忆
   - 超出时淘汰最旧的条目
   - 使用原子写入（tmp + rename）保证数据一致性

#### 3. `MemoryFactory`

根据配置创建对应后端实例的工厂类。

**位置**: `src/mini_agent/perception/memory_factory.py`

**内置后端**:
- `"local"` — MemoryStore（默认，无外部依赖）

**扩展点**（预留）:
- `"chroma"` — 向量检索（需要 chromadb）
- `"redis"` — 跨进程共享（需要 redis-py）
- `"sqlite"` — 关系型存储

## 配置方式

### 配置文件 (`agent_config.json`)

```json
{
  "memory_enabled": true,
  "memory_backend": "local",
  "memory_store_path": ".agent/memory.jsonl",
  "memory_top_k": 3,
  "memory_decay_half_life_days": 30.0,
  "memory_max_entries": 500
}
```

### 配置字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `memory_enabled` | bool | `false` | 是否启用记忆系统 |
| `memory_backend` | string | `"local"` | 后端类型（local/chroma/redis） |
| `memory_store_path` | string | `.agent/memory.jsonl` | 存储文件路径 |
| `memory_top_k` | int | `3` | 检索返回的最大条目数 |
| `memory_decay_half_life_days` | float | `30.0` | 时间衰减半衰期（天） |
| `memory_max_entries` | int | `500` | 记忆条目上限 |

### 代码中使用

```python
from mini_agent.config import load_config
from mini_agent.perception.memory_factory import create_memory_backend

# 1. 加载配置
cfg = load_config(memory_enabled=True, memory_top_k=5)

# 2. 创建后端实例
backend = create_memory_backend(cfg)

# 3. 添加记忆
from mini_agent.perception.memory_store import MemoryEntry
entry = MemoryEntry(
    session_id="session_123",
    summary="使用 TF-IDF 进行记忆检索",
    key_outcomes=["中文分词改用 n-gram", "加入时间衰减因子"],
    tags=["记忆系统", "TF-IDF", "中文处理"],
    model="claude-opus-4-5"
)
backend.add(entry)

# 4. 检索记忆
results = backend.search("如何处理中文文本", k=3)
for entry in results:
    print(f"{entry.summary} (score: {entry.age_days} days old)")

# 5. 按标签过滤
tagged = backend.search_by_tag("记忆系统")
```

## 记忆内容结构

### MemoryEntry 数据类

```python
@dataclass
class MemoryEntry:
    session_id: str           # 来源会话 ID
    summary: str              # 会话摘要（核心内容）
    key_outcomes: list[str]   # 关键结论列表
    tags: list[str]           # 自动提取的标签
    model: str                # 使用的模型名称
    created_at: float         # 创建时间戳
    entry_id: str             # 唯一标识符（自动生成）
```

### 记忆生成策略

建议由 Agent 在 session 结束时调用 `add()`，传入：

1. **summary**: 会话的核心目标和最终结果
2. **key_outcomes**: 具体的技术结论、配置参数、代码模式
3. **tags**: 自动从关键术语提取（如文件名、技术名词、错误类型）

示例记忆内容：
```python
MemoryEntry(
    session_id="20260608-abc123",
    summary="修复中文打印乱码问题，使用 UTF-8 编码并配置 console 字体",
    key_outcomes=[
        "在 Windows 上需要使用 chcp 65001 设置控制台为 UTF-8",
        "Python print 默认使用 GBK，需要显式指定 encoding='utf-8'",
        "设置 SUPPORTED_OS=Windows 以启用平台特定逻辑"
    ],
    tags=["中文", "乱码", "UTF-8", "Windows", "编码问题"],
    model="claude-opus-4-5"
)
```

## 可扩展性设计

### v2 可扩展性重构要点

1. **接口与实现分离**:
   - `MemoryBackend` 是纯抽象接口，不依赖任何具体存储
   - Agent 代码只依赖接口，完全解耦具体实现

2. **工厂模式**:
   - `MemoryFactory` 负责根据配置创建后端
   - 新增后端只需两步：
     1. 实现 `MemoryBackend` 接口
     2. 在 `_REGISTRY` 中注册

3. **配置模块化**:
   - `MemoryConfig` 独立配置记忆系统
   - 添加新参数不影响其他模块

### 添加自定义后端示例

```python
# 1. 创建自定义后端
class ChromaMemoryBackend(MemoryBackend):
    def __init__(self, cfg: AppConfig):
        import chromadb
        self._client = chromadb.PersistentClient(path=".chroma")
        self._collection = self._client.get_or_create_collection("memories")

    def add(self, entry: MemoryEntry) -> None:
        self._collection.add(
            documents=[entry.to_search_text()],
            metadatas=[{"session_id": entry.session_id, "tags": entry.tags}],
            ids=[entry.entry_id]
        )

    def search(self, query: str, k: int = 3) -> list[MemoryEntry]:
        results = self._collection.query(query_texts=[query], n_results=k)
        # 转换结果为 MemoryEntry 列表
        ...

    def search_by_tag(self, tag: str) -> list[MemoryEntry]:
        # Chroma 的过滤查询
        ...

    @property
    def count(self) -> int:
        return self._collection.count()

# 2. 注册后端
from mini_agent.perception.memory_factory import register_memory_backend

register_memory_backend("chroma", lambda cfg: ChromaMemoryBackend(cfg))

# 3. 在配置中使用
{
  "memory_backend": "chroma"
}
```

## 相关文档

- [系统架构总览](./system-overview.md) - Agent 整体架构
- [配置指南](./config-guide.md) - 完整配置说明
- [并发与任务编排](./plan-and-task-guide.md) - 多任务管理

## 常见问题

**Q: 为什么中文分词使用 n-gram 而不是逐字？**

A: 逐字切分会导致复合词（如"数据库"）被拆成独立的单字，每个单字的 IDF 权重极低，导致 TF-IDF 检索效果差。n-gram 保留了词语边界，能正确匹配"数据库"→["数据","据库","数据库"]。

**Q: 时间衰减的作用是什么？**

A: 记忆的价值随时间降低。指数衰减确保最近的记忆在检索时权重更高，防止旧记忆持续干扰当前上下文。

**Q: 记忆文件为什么会增长？**

A: 每次 session 结束都会添加新的记忆条目。超过 `max_entries`（默认 500）会自动淘汰最旧的条目。

**Q: 如何备份记忆？**

A: 记忆文件是 JSONL 格式，直接复制 `.agent/memory.jsonl` 即可。恢复时替换同名文件。
