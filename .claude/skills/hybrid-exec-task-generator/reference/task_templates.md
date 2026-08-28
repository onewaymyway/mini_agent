# hybrid_exec 常见任务类型 TaskSpec 模板库

> 对应 `next_doc/hybrid_exec_improvement_directions.md` B2。
>
> **这份文档管什么**：给几类在 `hybrid_exec_design_plan.md` 示例和
> `examples/hybrid_exec_demo.py` 里已经隐含出现过的"常见任务类型"，各
> 整理一份可以直接抄改的 `description` 措辞模板 + `output_validator`
> 参考实现 + 建议的 `allow_tiers` 组合，减少每次都要从零现场设计校验
> 逻辑的成本。`hybrid-exec-task-generator` skill 第二步"起草 TaskSpec"
> 时可以直接引用本文档里最贴近的一类，改字段值就用，而不是重新想一遍
> `output_validator` 该怎么写。
>
> **不管什么**：不重新介绍 `TaskSpec` 各字段的通用含义（见 SKILL.md
> 第二步的字段表 / `src/mini_agent/hybrid_exec/spec.py`），本文档只给
> "按任务类型分类"的具体填法参考。这些模板不是唯一正确写法，只是一个
> 比空白页更好的起点——真实任务的 `description`/校验粒度仍需要按实际
> 需求调整。
>
> **怎么用**：内容型文档，随时可以继续补充新的任务类型，不要求一次
> 穷尽。新增一类时，照着已有条目的结构（"适用场景 / description 措辞
> 模板 / output_validator 参考实现 / 建议 allow_tiers / 备注"）补一节
> 即可，不需要改动其它条目。

---

## 1. 结构化信息抽取

**适用场景**：从一段自由文本/半结构化文本里抽取若干个命名字段，产出
固定 schema 的 dict（如实体抽取、字段提取、简历解析）。

**`description` 措辞模板**：
```
从输入文本（ctx.params["text"]）中抽取<抽取目标，如"人名和机构名">，
返回 JSON 对象：{"<字段1>": [...], "<字段2>": [...]}。找不到时对应字段
返回空列表，不要编造不存在的内容；忽略<需要排除的干扰项，如"网址/邮箱">。
```

**`output_validator` 参考实现**（通用"dict 且包含指定 key、每个 key
的值是 list"校验，适配大多数抽取类任务）：
```python
def make_extraction_validator(required_keys: "list[str]"):
    def _validator(output):
        if not isinstance(output, dict):
            return False, f"期望 dict，实际 {type(output).__name__}"
        missing = [k for k in required_keys if k not in output]
        if missing:
            return False, f"缺少字段：{missing}"
        not_list = [k for k in required_keys if not isinstance(output.get(k), list)]
        if not_list:
            return False, f"以下字段应为 list：{not_list}"
        return True, "字段齐全且类型正确"
    return _validator

# 用法：output_validator=make_extraction_validator(["entities"])
```

**建议 `allow_tiers`**：`(SCRIPT, LLM)`——抽取逻辑一旦稳定通常能用规则/
正则脚本化，不太需要每次都上 Agent；输入文本结构差异很大（比如同时
处理中英文混排、多种版式）时再考虑加 `AGENT`。

---

## 2. 文本摘要

**适用场景**：把一段较长文本压缩成固定长度/固定要点数的摘要。

**`description` 措辞模板**：
```
把输入文本（ctx.params["text"]）压缩成不超过<N>字的摘要，保留<必须
保留的信息，如"关键数字/结论">，忽略<可以舍弃的内容，如"背景铺垫">。
直接返回摘要字符串本身，不要加"摘要："之类的前缀。
```

**`output_validator` 参考实现**（长度上限 + 非空校验；"摘要质量"本身
很难自动化校验，弱校验够用，质量依赖前几次人工抽查）：
```python
def make_summary_validator(max_chars: int, min_chars: int = 1):
    def _validator(output):
        if not isinstance(output, str):
            return False, f"期望 str，实际 {type(output).__name__}"
        length = len(output.strip())
        if length < min_chars:
            return False, "摘要为空或过短"
        if length > max_chars:
            return False, f"摘要超长：{length} 字符，上限 {max_chars}"
        return True, f"长度 {length} 字符，符合上限 {max_chars}"
    return _validator

# 用法：output_validator=make_summary_validator(max_chars=200)
```

**建议 `allow_tiers`**：`(LLM,)`——摘要生成本身高度依赖语言理解，脚本
化难度大、收益不明显（除非是"取前 N 句"这种规则型摘要，那种情况可以
尝试 `(SCRIPT, LLM)`），一般不需要 `AGENT`。

---

## 3. 格式转换

**适用场景**：把一种数据格式转成另一种（CSV→JSON、Markdown 表格→dict
列表、非标准日期字符串→ISO-8601……），输入输出结构都相对明确。

**`description` 措辞模板**：
```
把输入的<源格式，如"CSV 文本">（ctx.params["<字段名>"]）转换成
<目标格式，如"JSON 数组，每行一个 dict">。字段映射：<源字段名 →
目标字段名的对应关系>。<格式异常/缺失字段时的处理方式，如"跳过该行
并在返回结果里附一个 skipped_count 字段">。
```

**`output_validator` 参考实现**（校验顶层结构 + 每条记录的必需字段，
适合"转换成 list[dict]"这类最常见的目标形态）：
```python
def make_conversion_validator(required_record_keys: "list[str]"):
    def _validator(output):
        if not isinstance(output, list):
            return False, f"期望 list，实际 {type(output).__name__}"
        for i, record in enumerate(output):
            if not isinstance(record, dict):
                return False, f"第 {i} 条记录不是 dict"
            missing = [k for k in required_record_keys if k not in record]
            if missing:
                return False, f"第 {i} 条记录缺少字段：{missing}"
        return True, f"共 {len(output)} 条记录，结构正确"
    return _validator

# 用法：output_validator=make_conversion_validator(["name", "date"])
```

**建议 `allow_tiers`**：`(SCRIPT, LLM)`——格式转换规则一旦确定通常是
纯确定性逻辑，是 hybrid_exec 里最容易稳定命中脚本、复用价值最高的一类；
源格式本身就不规范（大量脏数据、格式不统一）时才需要偏重 LLM。

---

## 4. 简单分类判断

**适用场景**：给输入打一个标签/做一个二选一或多选一判断（情感分类、
垃圾内容识别、优先级打分）。

**`description` 措辞模板**：
```
判断输入（ctx.params["<字段名>"]）属于以下哪一类：<枚举所有候选类别，
逐个给出判定标准>。返回 JSON：{"label": "<候选类别之一>", "reason":
"<一句话理由>"}。不确定时选择<兜底类别，如"unknown"或"待人工判断">，
不要强行凑一个不合适的类别。
```

**`output_validator` 参考实现**（枚举值校验，`label` 必须落在给定候选
集合内）：
```python
def make_classification_validator(allowed_labels: "list[str]"):
    def _validator(output):
        if not isinstance(output, dict) or "label" not in output:
            return False, "期望 dict 且含 label 字段"
        label = output["label"]
        if label not in allowed_labels:
            return False, f"label={label!r} 不在允许集合 {allowed_labels} 内"
        return True, f"label={label!r} 合法"
    return _validator

# 用法：output_validator=make_classification_validator(["positive", "negative", "neutral"])
```

**建议 `allow_tiers`**：`(SCRIPT, LLM)`——如果分类标准能写成明确规则
（关键词匹配/数值阈值），脚本化命中率很高；涉及语义/情感理解的分类
通常更依赖 LLM，脚本层面很难稳定复现，可以考虑直接 `(LLM,)` 起步、
观察是否真的需要脚本兜底再加回 `SCRIPT`。

---

## 5. 网页内容解析

**适用场景**：给定一段 HTML（或已抓取好的网页文本），解析出结构化字段
（标题、正文、发布时间、价格……）。这类任务的典型痛点是"页面结构常变，
选择器容易过期"，对应 §1（hybrid_exec 现状盘点）里提到的 SKILL 档更
适合的场景——如果预期页面结构会频繁变化，优先考虑 `allow_tiers` 里加
`SKILL`（playbook 驱动），而不是死磕脚本层。

**`description` 措辞模板**：
```
从输入 HTML（ctx.params["html"]）中解析出：<逐个列出目标字段及其在
页面上的大致位置特征，如"标题：<h1> 标签内文本"、"价格：class 含
'price' 的元素，去掉货币符号后转成 float">。返回 JSON：{"<字段1>": ...,
"<字段2>": ...}。找不到的字段返回 null，不要用其它内容顶替。
```

**`output_validator` 参考实现**（复用"结构化信息抽取"的 dict-key
校验，但改为允许 `None` 值——网页解析里"字段确实不存在"是正常情况，
不应该被当成失败）：
```python
def make_parse_validator(required_keys: "list[str]"):
    def _validator(output):
        if not isinstance(output, dict):
            return False, f"期望 dict，实际 {type(output).__name__}"
        missing = [k for k in required_keys if k not in output]
        if missing:
            return False, f"缺少字段（可以是 null，但不能整个 key 都没有）：{missing}"
        return True, "字段齐全"
    return _validator

# 用法：output_validator=make_parse_validator(["title", "price", "published_at"])
```

**建议 `allow_tiers`**：
- 页面结构稳定（内部系统、固定模板）：`(SCRIPT, LLM)`，选择器脚本化
  收益明显。
- 页面结构容易变（第三方站点、常改版）：加上 `SKILL`——如
  `TaskSpec(..., allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM,
  ExecutionTier.SKILL, ExecutionTier.AGENT))`，并在
  `default_executor(..., enable_skill_tier=True, skill_max_turns=<N>)`
  里显式启用 SKILL 档（默认不启用，见
  `executor.py::default_executor` 的说明）。SKILL 档产出的是"人类可读
  步骤说明"而不是脚本，不会因为选择器变了就直接报废。

---

## 备注：如何往这份文档里继续补充

新增一类任务模板时：

1. 找一个"适用场景"描述清楚、与已有 5 类不重叠（或明显是某一类的特化
   变体，值得单独列）的任务类型。
2. 按上面的四段式结构（适用场景 / `description` 措辞模板 /
   `output_validator` 参考实现 / 建议 `allow_tiers`）补一节，模板里的
   占位符（`<...>`）保持和已有条目一致的风格，方便直接抄改。
3. `output_validator` 参考实现优先给"工厂函数"形式（`make_xxx_validator
   (参数) -> Callable`），而不是写死具体字段名的一次性函数——不同任务
   的具体字段名不一样，工厂函数模式能让同一份模板服务更多具体任务，
   减少每次都要重写一遍校验逻辑框架的成本。
4. 不确定某类任务该配哪档 `allow_tiers` 时，参考判断依据："规则/确定性
   逻辑占主导" → 偏 `SCRIPT`；"语义理解占主导" → 偏 `LLM`；"环境/页面
   结构不稳定但整体流程稳定" → 考虑加 `SKILL`；"需要多轮探查环境才能
   完成" → 才考虑 `AGENT`（成本最高，谨慎加）。
