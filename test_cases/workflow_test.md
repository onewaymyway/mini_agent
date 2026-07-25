# 工作流系统测试说明

## 功能概述

测试 mini_agent 工作流系统的完整功能，验证：
- YAML 定义的解析与保存（WorkflowDef / WorkflowStore）
- 步骤执行引擎：拓扑排序、占位符替换、条件判断（WorkflowRunner）
- 质检门：evaluator 角色绑定、评分提取、GATE_FAILED 状态、retry 重跑
- LLM 自动生成工作流（WorkflowGenerator）
- 6 个内置工具在主 Agent 对话中的完整使用流程
- [P10] 单 step 沙箱测试（`test_workflow_step`）、一次性执行覆盖
  （`resume_workflow_run(step_overrides=...)`）、watchdog 连续同类失败
  提前升级 `NEEDS_FIX`

---

## 前置条件

1. 已完成 workflow 模块安装（`src/mini_agent/workflow/` 目录存在）
2. `pyyaml` 已安装：`pip install pyyaml --break-system-packages`
3. 示例工作流存在：`.agent/workflows/code_review.yaml`（单文件模式）、
   `.agent/workflows/doc_change_review/`（文件夹模式，含私有
   `agents/`/`skills/`/`prompts/`）
4. 启动 agent：
   ```bash
   cd <project_root>
   PYTHONPATH=src python -m mini_agent
   ```
5. 启动日志应出现：
   ```
   Workflow tools registered (generate/save/run/list/show/delete_workflow)
   ```

---

## 单元测试（代码层，不调用 LLM）

### 测试一：WorkflowDef 解析与校验

```bash
PYTHONPATH=src python3 -c "
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepStatus

# 1. from_dict 完整解析
data = {
    'name': 'test_flow',
    'description': '测试工作流',
    'version': '1.0',
    'steps': [
        {'id': 'step1', 'name': '第一步', 'prompt': '分析 {code}'},
        {
            'id': 'step2', 'name': '第二步',
            'prompt': '基于 {step1.output} 生成报告',
            'depends_on': ['step1'],
            'role': 'evaluator',
            'retry_on_gate_fail': 2,
        },
        {
            'id': 'step3', 'name': '第三步',
            'prompt': '发布：{step2.output}',
            'depends_on': ['step2'],
            'condition': 'step2.score >= 60',
        },
    ]
}

wf = WorkflowDef.from_dict(data)
assert wf.name == 'test_flow'
assert len(wf.steps) == 3
assert wf.steps[1].role == 'evaluator'
assert wf.steps[1].retry_on_gate_fail == 2
assert wf.steps[2].condition == 'step2.score >= 60'
print('✅ from_dict 完整解析通过')

# 2. validate 正常
errors = wf.validate()
assert not errors, f'期望无错误，得到：{errors}'
print('✅ validate 正常工作流通过')

# 3. validate 检测 id 重复
from mini_agent.workflow.schema import WorkflowStep
bad_wf = WorkflowDef(name='bad', steps=[
    WorkflowStep(id='dup', name='A', prompt='x'),
    WorkflowStep(id='dup', name='B', prompt='y'),
])
errors = bad_wf.validate()
assert any('重复' in e for e in errors), f'期望检测到重复 id，实际错误：{errors}'
print('✅ validate 检测 id 重复')

# 4. validate 检测依赖不存在
bad_wf2 = WorkflowDef(name='bad2', steps=[
    WorkflowStep(id='s1', name='S1', prompt='x', depends_on=['nonexistent']),
])
errors2 = bad_wf2.validate()
assert any('nonexistent' in e for e in errors2), f'期望检测到缺失依赖，实际：{errors2}'
print('✅ validate 检测依赖不存在')

# 5. StepStatus.GATE_FAILED 存在
assert StepStatus.GATE_FAILED == 'gate_failed'
print('✅ StepStatus.GATE_FAILED 存在')
"
```

**期望输出**：5 个 ✅

---

### 测试二：WorkflowStore 读写

```bash
PYTHONPATH=src python3 -c "
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep
from pathlib import Path
import tempfile, os

with tempfile.TemporaryDirectory() as tmpdir:
    store = WorkflowStore(Path(tmpdir))

    # 1. 保存工作流
    wf = WorkflowDef(
        name='my-test',
        description='测试工作流',
        steps=[
            WorkflowStep(id='a', name='步骤A', prompt='输入：{topic}'),
            WorkflowStep(id='b', name='步骤B', prompt='基于 {a.output}', depends_on=['a']),
        ]
    )
    path = store.save(wf)
    assert path.exists(), '文件应该存在'
    print(f'✅ 保存成功：{path.name}')

    # 2. 加载工作流
    wf2 = store.load('my-test')
    assert wf2 is not None, '应该能加载'
    assert wf2.name == 'my-test'
    assert len(wf2.steps) == 2
    assert wf2.steps[1].depends_on == ['a']
    print('✅ 加载成功，字段完整')

    # 3. list_all
    lst = store.list_all()
    assert len(lst) == 1
    assert lst[0]['name'] == 'my-test'
    assert lst[0]['step_count'] == 2
    print('✅ list_all 返回正确')

    # 4. exists
    assert store.exists('my-test')
    assert not store.exists('nonexistent')
    print('✅ exists 判断正确')

    # 5. export_yaml
    yaml_str = store.export_yaml('my-test')
    assert yaml_str is not None
    assert 'my-test' in yaml_str
    print('✅ export_yaml 正常')

    # 6. delete
    store.delete('my-test')
    assert not store.exists('my-test')
    print('✅ 删除成功')

    # 7. 加载 code_review 示例
    real_store = WorkflowStore(Path('.'))
    code_review = real_store._load_path(Path('.agent/workflows/code_review.yaml'))
    assert code_review is not None
    assert code_review.name == 'code_review'
    assert len(code_review.steps) == 4
    eval_step = next(s for s in code_review.steps if s.id == 'evaluate')
    assert eval_step.role == 'evaluator'
    assert eval_step.retry_on_gate_fail == 1
    print(f'✅ code_review 示例加载：{[s.id for s in code_review.steps]}')
"
```

**期望输出**：7 个 ✅

---

### 测试三：WorkflowRunner 核心逻辑（Mock 执行）

```bash
PYTHONPATH=src python3 -c "
import sys
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
from mini_agent.workflow.runner import WorkflowRunner

class MockRunner(WorkflowRunner):
    '''不调用 LLM，根据步骤 id 返回预设输出'''
    def _execute_step(self, step, prompt, step_results):
        import time
        outputs = {
            'analyze': '代码结构分析完毕，发现 2 处潜在问题',
            'review':  '深度审查：存在除零风险和类型不安全',
            'evaluate': 'SCORE: 8/10\n内容全面，结构清晰',
            'report':  '## 代码审查报告\n\n问题已汇总...',
        }
        output = outputs.get(step.id, f'[{step.id} 执行完成]')
        from mini_agent.role_agents.feedback import extract_score
        score = extract_score(output) if step.role == 'evaluator' else None
        return StepResult(step.id, StepStatus.DONE, output=output, score=score, duration_seconds=0.01)

class FakeCfg:
    project_root='/tmp'; verbose=False; sandbox=False
    model='test'; llm_provider='anthropic'; llm_base_url=None; api_key='test'

import yaml
with open('.agent/workflows/code_review.yaml') as f:
    wf = WorkflowDef.from_dict(yaml.safe_load(f))

runner = MockRunner(FakeCfg())

# 1. 拓扑排序
ordered = runner._topological_sort(wf)
order = [s.id for s in ordered]
assert order.index('analyze') < order.index('review'), '依赖顺序错误'
assert order.index('review') < order.index('evaluate'), '依赖顺序错误'
assert order.index('evaluate') < order.index('report'), '依赖顺序错误'
print(f'✅ 拓扑排序：{order}')

# 2. 占位符替换
step_results = {'analyze': StepResult('analyze', StepStatus.DONE, output='ABC分析结果')}
inputs = {'code': 'def foo(): pass'}
resolved = runner._resolve_prompt('{analyze.output} | 代码：{code}', step_results, inputs)
assert 'ABC分析结果' in resolved
assert 'def foo()' in resolved
print('✅ 占位符替换正确')

# 3. 评分引用（{step.score}）
step_results['evaluate'] = StepResult('evaluate', StepStatus.DONE, output='x', score=0.85)
resolved2 = runner._resolve_prompt('评分：{evaluate.score}/100', step_results, {})
assert '85/100' in resolved2
print('✅ 评分占位符替换正确')

# 4. 条件判断
import types
step_results['evaluate'] = StepResult('evaluate', StepStatus.DONE, output='x', score=0.72)
assert runner._eval_condition('evaluate.score >= 60', step_results) == True
assert runner._eval_condition('evaluate.score >= 80', step_results) == False
print('✅ 条件判断正确（72/100：>= 60 通过，>= 80 不通过）')

# 5. 完整运行
result = runner.run(wf, {'code': 'def calculate(a, b): return a/b'})
assert result.status == 'done'
done_steps = [sr.step_id for sr in result.step_results if sr.status == StepStatus.DONE]
assert set(done_steps) == {'analyze', 'review', 'evaluate', 'report'}
print(f'✅ 完整运行：status={result.status}, 步骤={done_steps}')

# 6. 条件不满足时跳过步骤（低分场景）
class LowScoreRunner(MockRunner):
    def _execute_step(self, step, prompt, step_results):
        from mini_agent.role_agents.feedback import extract_score
        if step.id == 'evaluate':
            output = 'SCORE: 3/10\n内容质量较差'
            score = extract_score(output)
            return StepResult(step.id, StepStatus.DONE, output=output, score=score)
        return super()._execute_step(step, prompt, step_results)

result2 = LowScoreRunner(FakeCfg()).run(wf, {'code': 'x'})
report_sr = next(sr for sr in result2.step_results if sr.step_id == 'report')
assert report_sr.status == StepStatus.SKIPPED, f'期望 SKIPPED，实际 {report_sr.status}'
print('✅ 条件不满足时步骤被跳过（低分 report 跳过）')
"
```

**期望输出**：6 个 ✅

---

### 测试四：质检门（GATE_FAILED + retry）

```bash
PYTHONPATH=src python3 -c "
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
from mini_agent.workflow.runner import WorkflowRunner

call_log = []

class GateTestRunner(WorkflowRunner):
    def _extract_step_score(self, step, output):
        from mini_agent.role_agents.feedback import extract_score
        return extract_score(output) if 'evaluator' in (step.role or '') else None

    def _get_gate_threshold(self, step):
        return 0.75 if 'evaluator' in (step.role or '') else None

    def _execute_step(self, step, prompt, step_results):
        call_log.append(step.id)
        from mini_agent.role_agents.feedback import extract_score
        import time

        if step.id == 'write':
            # 第一次写得差，第二次（含反馈）写得好
            n = call_log.count('write')
            output = '质量较差的草稿' if n == 1 else '根据反馈改进后的高质量版本'
            return StepResult(step.id, StepStatus.DONE, output=output, duration_seconds=0.01)

        elif step.id == 'evaluate':
            n = call_log.count('evaluate')
            output = 'SCORE: 5/10\n内容不够完整' if n == 1 else 'SCORE: 9/10\n改进后内容优秀'
            score = extract_score(output)
            threshold = self._get_gate_threshold(step)
            if score and threshold and score < threshold:
                return StepResult(step.id, StepStatus.GATE_FAILED, output=output, score=score,
                    error=f'质检不达标：{int(score*100)}/100', duration_seconds=0.01)
            return StepResult(step.id, StepStatus.DONE, output=output, score=score, duration_seconds=0.01)

        else:
            return StepResult(step.id, StepStatus.DONE, output=f'{step.id} 完成', duration_seconds=0.01)

class FakeCfg:
    project_root='/tmp'; verbose=False; sandbox=False
    model='t'; llm_provider='anthropic'; llm_base_url=None; api_key='t'

wf = WorkflowDef(name='gate_test', steps=[
    WorkflowStep(id='write', name='写作', prompt='写关于 {topic} 的文章'),
    WorkflowStep(id='evaluate', name='质检', prompt='评估：{write.output}',
                 role='evaluator', depends_on=['write'], retry_on_gate_fail=1),
    WorkflowStep(id='publish', name='发布', prompt='发布：{write.output}',
                 depends_on=['evaluate']),
])

runner = GateTestRunner(FakeCfg())
result = runner.run(wf, {'topic': 'AI'})

print(f'调用记录：{call_log}')
print(f'最终状态：{result.status}')

# 断言
assert call_log.count('write') == 2, f'write 应被调用 2 次，实际 {call_log.count(\"write\")} 次'
assert call_log.count('evaluate') == 2, f'evaluate 应被调用 2 次，实际 {call_log.count(\"evaluate\")} 次'
assert result.status == 'done', f'期望 done，实际 {result.status}'

publish_sr = next(sr for sr in result.step_results if sr.step_id == 'publish')
assert publish_sr.status == StepStatus.DONE, f'publish 应为 DONE，实际 {publish_sr.status}'

final_write = next(sr for sr in result.step_results if sr.step_id == 'write')
assert '改进后' in final_write.output, f'write 最终输出应为改进后版本，实际：{final_write.output}'

print('✅ write 被重跑一次（含评估反馈）')
print('✅ evaluate 运行两次（第二次通过）')
print('✅ publish 正常执行')
print('✅ 质检门 + retry 完整流程通过')

# 摘要中应有正确图标
summary = result.to_summary()
print()
print(summary[:500])
"
```

**期望输出**：
```
调用记录：['write', 'evaluate', 'write', 'evaluate', 'publish']
最终状态：done
✅ write 被重跑一次（含评估反馈）
✅ evaluate 运行两次（第二次通过）
✅ publish 正常执行
✅ 质检门 + retry 完整流程通过
```

---

## 场景测试（对话层）

### 场景一：list_workflows — 查看已有工作流

**测试步骤**：
在 agent 对话中输入：
```
列出所有可用的工作流
```

**期望行为**：
- agent 调用 `list_workflows` 工具
- 返回包含 `code_review` 工作流的列表
- 展示步骤数（4）和步骤顺序（analyze → review → evaluate → report）

**期望输出示例**：
```
📋 共 1 个工作流：

**code_review** (v1.0)
  描述：代码审查完整流程，包括分析、深度审查、质量评估和报告生成
  步骤：analyze → review → evaluate → report
```

---

### 场景二：show_workflow — 查看 YAML 定义

**测试步骤**：
```
用 show_workflow 工具查看 code_review 工作流的定义
```

**期望行为**：
- 返回完整 YAML，包含 4 个步骤的定义
- `evaluate` 步骤的 `role: evaluator` 和 `retry_on_gate_fail: 1` 可见
- `report` 步骤的 `condition: "evaluate.score >= 40"` 可见

---

### 场景三：run_workflow — 执行示例工作流

**测试步骤**：
```
执行 code_review 工作流，审查以下代码：

def divide(a, b):
    return a / b

result = divide(10, 0)
print(result)
```

**期望行为**：
- agent 调用 `run_workflow("code_review", {"code": "def divide(a, b):\n    return a / b\n..."})` 
- 控制台出现逐步执行日志：
  ```
  [Workflow] 开始执行：code_review（共 4 步）
  [Workflow] 步骤：analyze（静态分析）
  [Workflow] ✅ 步骤 analyze 完成 (X.Xs)
  [Workflow] 步骤：review（深度审查）
  ...
  ```
- 最终返回 `## 工作流执行结果：code_review` 摘要
- `evaluate` 步骤显示评分（如 `评分：XX/100`）
- 如果评分低（被测代码有明显缺陷），可能出现 `🔄` 标志触发重试

---

### 场景四：generate_workflow — LLM 自动生成

**测试步骤**：
```
生成一个工作流：对用户提供的文章进行翻译（中译英），包括初译、润色和质量审核三个步骤
```

**期望行为**：
1. agent 调用 `generate_workflow`，LLM 生成 YAML
2. 返回工作流预览，格式类似：
   ```
   ## 工作流预览：article_translator
   描述：文章中译英翻译流程
   版本：1.0  步骤数：3
   
   ### 步骤列表
   1. **translate** — 初译 ...
   2. **polish** — 润色 ...
   3. **quality_check** — 质量审核 [角色:evaluator] ...
   ```
3. 同时展示完整 YAML
4. 提示"如果满意，调用 save_workflow 保存"

**验证 YAML 合法性**（将 agent 生成的 YAML 复制后执行）：

```bash
PYTHONPATH=src python3 -c "
from mini_agent.workflow.generator import WorkflowGenerator
from pathlib import Path

class FakeCfg:
    project_root='/tmp'; verbose=False; sandbox=False; model='t'
    llm_provider='anthropic'; llm_base_url=None; api_key='t'

# 手动写一个测试 YAML（模拟 LLM 生成结果）
test_yaml = '''
name: article_translator
description: 文章中译英翻译流程
version: \"1.0\"
steps:
  - id: translate
    name: 初译
    prompt: |
      请将以下中文文章翻译为英文：
      {article}
  - id: polish
    name: 润色
    prompt: |
      请对以下英文译文进行润色，使其更地道：
      {translate.output}
    depends_on: [translate]
  - id: quality_check
    name: 质量审核
    prompt: |
      请评估以下翻译质量（必须输出 SCORE: x/10）：
      {polish.output}
    depends_on: [polish]
    role: evaluator
'''

gen = WorkflowGenerator(FakeCfg())
wf = gen.parse_yaml(test_yaml)
errors = wf.validate()
assert not errors, f'校验错误：{errors}'
print(f'✅ 生成的 YAML 格式合法：{[s.id for s in wf.steps]}')
print(gen.preview(wf))
"
```

---

### 场景五：save_workflow → run_workflow 完整链路

**测试步骤（连续对话）**：

1. 生成工作流：
   ```
   生成一个简单的工作流：先总结一段文字，再评估总结质量
   ```

2. 确认后保存：
   ```
   看起来不错，请保存这个工作流
   ```

3. 验证保存成功：
   ```
   列出所有工作流
   ```
   （应出现新保存的工作流）

4. 执行新工作流：
   ```
   运行这个工作流，文字内容是："人工智能正在改变我们的工作方式，自动化正在取代重复性劳动，
   同时创造出新的职业机会需要更高阶的认知能力。"
   ```

**期望行为**：
- 步骤1：生成 YAML，展示预览
- 步骤2：`save_workflow` 成功，返回"✅ 工作流 xxx 已保存"
- 步骤3：列表中出现新工作流
- 步骤4：工作流按步骤执行，返回完整摘要

---

### 场景六：手动编写 YAML 并执行

**目的**：验证用户手动创建 YAML 文件后可以直接运行，无需通过 `generate_workflow`。

**步骤**：

1. 在文件系统创建 `.agent/workflows/keyword_extractor.yaml`：

   ```yaml
   name: keyword_extractor
   description: 从文章中提取关键词并分类
   version: "1.0"
   steps:
     - id: extract
       name: 提取关键词
       prompt: |
         从以下文章中提取 10 个最重要的关键词：
         {article}

     - id: categorize
       name: 分类
       prompt: |
         将以下关键词按「技术」「概念」「实体」分类：
         {extract.output}
       depends_on: [extract]
   ```

2. 在 agent 对话中执行：
   ```
   运行 keyword_extractor 工作流，文章内容是："……（任意一段文字）……"
   ```

**期望行为**：
- 无需重启 agent，直接可用
- 按步骤执行并返回摘要

---

### 场景七：输入参数缺失时的降级行为

**目的**：验证 prompt 中的占位符在 `inputs` 里没有对应值时，保持原样而非报错。

**测试步骤**：
```
运行 code_review 工作流，不传任何参数
```
（不提供 `code` 参数）

**期望行为**：
- 工作流仍然执行，`{code}` 占位符保持原样出现在 prompt 里
- 主 Agent 会尝试处理含 `{code}` 字面文本的 prompt，可能返回提示"未提供代码"
- **不会抛出 Python 异常**，工作流继续执行或优雅报告步骤失败

---

## 文件夹模式 Workflow 测试（doc_change_review）

> 对应 `next_doc/workflow_directory_mode_design.md`，验证"文件夹模式"
> workflow（`.agent/workflows/<name>/workflow.yaml` + 私有
> `agents/`/`skills/`/`prompts/`）的解析、资源合并与执行。示例工作流：
> `.agent/workflows/doc_change_review/`（文档变更审查流水线，四个 step
> 分别覆盖 `agent`/`skill_agent`/`role_agent`/`agent`+`condition`）。

前置条件：额外确认 `.agent/workflows/doc_change_review/` 目录存在，
包含 `workflow.yaml`、`agents/reviewer.md`、`skills/changelog-diff/SKILL.md`、
`prompts/{collect,review,report}.md`。

### 单元测试五：文件夹模式加载与校验

```bash
PYTHONPATH=src python3 -c "
from pathlib import Path
from mini_agent.workflow.store import WorkflowStore

store = WorkflowStore(project_root=Path('.'))
wf = store.load('doc_change_review')

# 1. source_dir 指向文件夹，而不是 None（单文件模式才是 None）
assert wf.source_dir is not None
assert Path(str(wf.source_dir)).name == 'doc_change_review'
print(f'✅ source_dir 指向文件夹：{wf.source_dir}')

# 2. 四个 step 都被正确解析
ids = [s.id for s in wf.steps]
assert ids == ['collect', 'diff', 'review', 'report'], ids
print(f'✅ 四个 step 顺序正确：{ids}')

# 3. type / role / skill_name 各自到位
by_id = {s.id: s for s in wf.steps}
assert by_id['collect'].effective_type == 'agent'
assert by_id['diff'].effective_type == 'skill_agent' and by_id['diff'].skill_name == 'changelog-diff'
assert by_id['review'].effective_type == 'role_agent' and by_id['review'].role == 'reviewer'
assert by_id['report'].effective_type == 'agent'
print('✅ 四个 step 的 type/role/skill_name 均正确')

# 4. prompt_file 在加载阶段被读出内容填充进 prompt
assert by_id['collect'].prompt_file == 'prompts/collect.md'
assert by_id['collect'].prompt.strip(), 'prompt_file 内容应该已经填充进 prompt'
assert '{old_path}' in by_id['collect'].prompt
print('✅ prompt_file 内容已正确读取填充')

# 5. validate() 无错误
errors = wf.validate()
assert not errors, f'期望无错误，得到：{errors}'
print('✅ validate 通过，无校验错误')
"
```

**期望输出**：5 个 ✅

---

### 单元测试六：本地资源合并（WorkflowResourceBundle）

```bash
PYTHONPATH=src python3 -c "
from pathlib import Path
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow.resource_bundle import build_resource_bundle

store = WorkflowStore(project_root=Path('.'))
wf = store.load('doc_change_review')

class FakeCfg:
    project_root = '.'
    skills_dir = None

bundle = build_resource_bundle(FakeCfg(), wf)
assert bundle is not None, '文件夹模式应该能构造出 bundle'
print('✅ 文件夹模式成功构造 WorkflowResourceBundle')

# 1. 本地 agent profile 'reviewer' 被发现，且与全局 agents 合并在同一个 loader 里
assert 'reviewer' in bundle.agent_loader.available
print(f'✅ agent_loader 发现本地 reviewer，可用列表：{bundle.agent_loader.available}')

# 2. 本地 skill 'changelog-diff' 被发现
assert 'changelog-diff' in bundle.skill_loader.available
print(f'✅ skill_loader 发现本地 changelog-diff，可用列表：{bundle.skill_loader.available}')

# 3. 单文件模式（source_dir=None）应该返回 None，不构造 bundle
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep
flat_wf = WorkflowDef(name='flat', steps=[WorkflowStep(id='a', name='A', prompt='x')])
assert flat_wf.source_dir is None
assert build_resource_bundle(FakeCfg(), flat_wf) is None
print('✅ 单文件模式（source_dir=None）不构造 bundle，返回 None')
"
```

**期望输出**：3 个 ✅

---

### 场景测试八：运行 doc_change_review 文件夹模式工作流

**测试步骤**：
在 agent 对话中输入（把 `<old>`/`<new>` 换成任意两个可读的本地文本文件路径，
没有现成文件也可以先用 `write_file` 临时造两份内容有差异的文本）：
```
运行 doc_change_review 工作流，old_path 是 <old>，new_path 是 <new>
```

**期望行为**：
- agent 调用 `run_workflow("doc_change_review", {"old_path": "...", "new_path": "..."})`
- 控制台按拓扑顺序执行 4 个 step：`collect → diff → review → report`
- `collect` 步骤由主 Agent 执行（读取 `prompts/collect.md` 展开后的 prompt）
- `diff` 步骤日志中能看到只强制挂载了 `changelog-diff` skill 的执行痕迹
  （不依赖关键词触发判断）
- `review` 步骤按 `agents/reviewer.md`（本工作流私有 profile，不是全局
  `.agent/agents/` 目录下的同名文件，如果两处都存在同名 profile）执行
- 最终 `report` 步骤汇总前面三步结果，输出"## 文档变更审查报告"
- 全程不需要重启 agent、不需要额外配置

**辅助验证（CLI）**：
```
/workflow show doc_change_review
```
应展示完整 YAML，`diff` 步骤可见 `type: skill_agent` + `skill_name: changelog-diff`，
`review` 步骤可见 `type: role_agent` + `role: reviewer`。

---

### 场景测试九：`/workflow to-dir` 单文件 → 文件夹模式转换

**目的**：验证已有单文件工作流可以一键升级为文件夹模式，且转换后行为不变。

**测试步骤**：
```
/workflow to-dir code_review
```

**期望行为**：
- 生成 `.agent/workflows/code_review/workflow.yaml`（原 4 个 step 原样迁移）
- 自动创建空的 `.agent/workflows/code_review/agents/`、`skills/`、`prompts/`
- 原来的单文件 `.agent/workflows/code_review.yaml` 被删除
- `/workflow show code_review` 结果与转换前一致（字段无损）
- `/workflow run code_review ...` 仍可正常执行，行为与转换前完全一致

**代码层验证**（可选，用临时目录避免污染仓库真实文件）：
```bash
PYTHONPATH=src python3 -c "
import tempfile
from pathlib import Path
from mini_agent.workflow.store import WorkflowStore

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / '.agent' / 'workflows').mkdir(parents=True)
    store = WorkflowStore(project_root=tmp)

    import shutil
    shutil.copy('.agent/workflows/code_review.yaml', tmp / '.agent' / 'workflows' / 'code_review.yaml')

    new_path = store.to_dir('code_review')
    new_dir = new_path.parent
    assert new_path.name == 'workflow.yaml' and new_path.exists()
    assert (new_dir / 'agents').is_dir()
    assert (new_dir / 'skills').is_dir()
    assert (new_dir / 'prompts').is_dir()
    assert not (tmp / '.agent' / 'workflows' / 'code_review.yaml').exists(), '原单文件应被删除'
    print('✅ to-dir 迁移完成：workflow.yaml + agents/skills/prompts 空目录')

    wf2 = store.load('code_review')
    assert wf2.source_dir is not None
    assert len(wf2.steps) == 4
    assert wf2.validate() == []
    print('✅ 迁移后重新加载，字段完整、校验通过')
"
```

**期望输出**：2 个 ✅

---

## P10 测试：调试闭环细化 + 看护趋势感知

> 对应 `next_doc/workflow_mechanism_improvement_plan_p10.md`（状态：已实现），
> 验证三项新能力：单 step 沙箱测试（`test_workflow_step`）、一次性执行覆盖
> （`resume_workflow_run(step_overrides=...)`）、watchdog 连续同类失败提前
> 升级 `NEEDS_FIX`。完整自动化用例见 `tests/test_workflow_p10.py`（14 个
> 用例），可直接运行：
> ```bash
> PYTHONPATH=src python3 -m pytest tests/test_workflow_p10.py -v
> ```
> 下面是等价的手工可读版本，便于不跑 pytest 时人工核对行为。

### 单元测试七：`test_workflow_step` 单 step 沙箱测试

```bash
PYTHONPATH=src python3 -c "
import tempfile
from pathlib import Path
from unittest.mock import patch
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow import api_helpers
from mini_agent.storage.paths import AgentPaths

class FakeWfCfg:
    tool_call_step_auto_approve=True
    human_input_wait_timeout_seconds=2.0
    approval_poll_interval_seconds=0.05
    approval_wait_timeout_seconds=1.0
    validate_placeholders_on_save=True
    validate_role_refs_on_save=True
    script_step_enabled=False
    max_sub_workflow_depth=3
    git_hint_enabled=False

class FakeCfg:
    def __init__(self, root):
        self.project_root=root; self.verbose=False; self.sandbox=False
        self.model='t'; self.llm_provider='anthropic'; self.llm_base_url=None; self.api_key='t'
        self.workflow=FakeWfCfg()

with tempfile.TemporaryDirectory() as tmp:
    cfg = FakeCfg(tmp)
    store = WorkflowStore(Path(tmp))

    # 1. 普通 agent 类型 step：mock 掉真实 LLM 调用，验证不落盘 + 占位符替换正确
    wf = WorkflowDef(name='wf_sandbox', steps=[
        WorkflowStep(id='fetch', name='fetch', prompt='fetch data'),
        WorkflowStep(id='analyze', name='analyze', prompt='analyze: {fetch.output}，语言 {lang}', depends_on=['fetch']),
    ])
    store.save(wf, cfg=cfg)

    paths = AgentPaths(project_root=tmp)
    sessions_dir = paths.workflow_sessions_dir
    before = list(sessions_dir.glob('**/*')) if sessions_dir.exists() else []

    with patch.object(WorkflowRunner, '_execute_with_main_agent', return_value='ok') as mock_exec:
        result = api_helpers.test_workflow_step(
            cfg, 'wf_sandbox', 'analyze',
            mock_step_results={'fetch': {'output': 'MOCK_DATA', 'passed': True}},
            mock_inputs={'lang': 'zh'},
        )
    assert result['skipped'] is False
    assert result['status'] == 'done'
    called_prompt = mock_exec.call_args[0][1]
    assert 'MOCK_DATA' in called_prompt and 'zh' in called_prompt
    after = list(sessions_dir.glob('**/*')) if sessions_dir.exists() else []
    assert before == after, '沙箱测试不应产生任何 workflow_sessions 落盘文件'
    print('✅ agent 类型 step 沙箱执行：不落盘、mock 数据正确替换占位符')

    # 2. human_input 类型 step：应直接提示跳过，不阻塞
    wf2 = WorkflowDef(name='wf_human', steps=[
        WorkflowStep(id='ask', name='ask', prompt='请输入', type='human_input'),
    ])
    store.save(wf2, cfg=cfg)
    result2 = api_helpers.test_workflow_step(cfg, 'wf_human', 'ask')
    assert result2['skipped'] is True
    assert 'resume_workflow_run' in result2['reason']
    print('✅ human_input 类型 step 沙箱测试按预期跳过，不阻塞')

    # 3. require_approval 类型 step：同样跳过
    wf3 = WorkflowDef(name='wf_approval', steps=[
        WorkflowStep(id='deploy', name='deploy', prompt='部署', require_approval=True),
    ])
    store.save(wf3, cfg=cfg)
    result3 = api_helpers.test_workflow_step(cfg, 'wf_approval', 'deploy')
    assert result3['skipped'] is True
    print('✅ require_approval 类型 step 沙箱测试按预期跳过')

    # 4. 缺少 mock 数据时报错提示清晰
    wf4 = WorkflowDef(name='wf_missing', steps=[
        WorkflowStep(id='a', name='a', prompt='do a'),
        WorkflowStep(id='b', name='b', prompt='use {a.output}', depends_on=['a']),
    ])
    store.save(wf4, cfg=cfg)
    try:
        api_helpers.test_workflow_step(cfg, 'wf_missing', 'b')
        raise AssertionError('应抛出 WorkflowApiError')
    except api_helpers.WorkflowApiError as e:
        assert e.code == 'bad_mock_data'
        print(f'✅ 缺少 mock 数据时报错清晰：{e.message[:40]}...')
"
```

**期望输出**：4 个 ✅

---

### 单元测试八：`resume_workflow_run(step_overrides=...)` 一次性执行覆盖

```bash
PYTHONPATH=src python3 -c "
import tempfile
from pathlib import Path
from unittest.mock import patch
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow.session import WorkflowSession
from mini_agent.workflow import api_helpers
from mini_agent.storage.paths import AgentPaths

class FakeWfCfg:
    parallel_enabled=True; max_parallel=4; hooks_enabled=False
    watchdog_enabled=False; retry_on_error_backoff_seconds=0.0
    background_execution_default=False; git_hint_enabled=False
    validate_placeholders_on_save=True; validate_role_refs_on_save=True

class FakeCfg:
    def __init__(self, root):
        self.project_root=root; self.verbose=False; self.sandbox=False
        self.model='t'; self.llm_provider='anthropic'; self.llm_base_url=None; self.api_key='t'
        self.workflow=FakeWfCfg()

with tempfile.TemporaryDirectory() as tmp:
    cfg = FakeCfg(tmp)
    store = WorkflowStore(Path(tmp))
    wf = WorkflowDef(name='wf_override', steps=[WorkflowStep(id='solo', name='solo', prompt='do it', timeout=30)])
    store.save(wf, cfg=cfg)

    with patch.object(WorkflowRunner, '_execute_with_main_agent', return_value='done'):
        wf_session_id = WorkflowRunner(cfg).run(wf, inputs={}).workflow_session_id

    before_yaml = store.export_yaml('wf_override')

    # 1. 合法字段（timeout）覆盖：不写回持久化 YAML
    with patch.object(WorkflowRunner, '_execute_with_main_agent', return_value='done again'):
        outcome = api_helpers.resume_workflow_run(cfg, wf_session_id, step_overrides={'solo': {'timeout': 999}})
    assert outcome['mode'] == 'sync'
    after_yaml = store.export_yaml('wf_override')
    assert before_yaml == after_yaml, 'step_overrides 不应写回持久化的 workflow 定义'

    paths = AgentPaths(project_root=tmp)
    s = WorkflowSession.load(paths, wf_session_id)
    assert s.last_step_overrides == {'solo': {'timeout': 999}}
    print('✅ 合法字段覆盖：持久化 YAML 不变，session 记录了本次覆盖用于展示')

    # 2. 非法字段（prompt）直接拒绝
    try:
        api_helpers.resume_workflow_run(cfg, wf_session_id, step_overrides={'solo': {'prompt': '改逻辑，不允许'}})
        raise AssertionError('应抛出 WorkflowApiError')
    except api_helpers.WorkflowApiError as e:
        assert e.code == 'bad_override'
        print(f'✅ 非法字段（prompt）被拒绝：{e.message[:50]}...')

    # 3. 引用不存在的 step_id 同样拒绝
    try:
        api_helpers.resume_workflow_run(cfg, wf_session_id, step_overrides={'no_such_step': {'timeout': 10}})
        raise AssertionError('应抛出 WorkflowApiError')
    except api_helpers.WorkflowApiError as e:
        assert e.code == 'bad_override'
        print('✅ 引用不存在的 step_id 被拒绝')
"
```

**期望输出**：3 个 ✅

---

### 单元测试九：Watchdog 连续同类失败提前升级 `NEEDS_FIX`

```bash
PYTHONPATH=src python3 -c "
import tempfile
from unittest.mock import patch
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.watchdog import WorkflowWatchdog
from mini_agent.workflow import registry as wf_registry
from mini_agent.storage.paths import AgentPaths

# 1. watchdog 层：连续同 error_type 达阈值才升级，不同类型打断计数
paths = AgentPaths(project_root=tempfile.mkdtemp())
control = wf_registry.register('wfs_p10_demo')
wd = WorkflowWatchdog(paths=paths, workflow_session_id='wfs_p10_demo', control=control)
assert wd.report_attempt_failure('s1', 'TimeoutError', threshold=2) is False
assert wd.report_attempt_failure('s1', 'TimeoutError', threshold=2) is True
print('✅ 连续 2 次同一 error_type 后触发升级')

wd2 = WorkflowWatchdog(paths=paths, workflow_session_id='wfs_p10_demo2', control=wf_registry.register('wfs_p10_demo2'))
assert wd2.report_attempt_failure('s1', 'TimeoutError', threshold=2) is False
assert wd2.report_attempt_failure('s1', 'ValueError', threshold=2) is False  # 类型不同，计数被打断
assert wd2.report_attempt_failure('s1', 'TimeoutError', threshold=2) is False  # 重新从 1 计数
print('✅ error_type 不同时不触发提前升级（计数被打断重新开始）')

# 2. 集成 runner._execute_step_with_error_retry：阈值=2 时第 2 次失败即短路进入 NEEDS_FIX
class FakeWfCfg:
    retry_on_error_backoff_seconds=0.0

class FakeCfg:
    project_root='/tmp'; verbose=False; sandbox=False
    model='t'; llm_provider='anthropic'; llm_base_url=None; api_key='t'
    workflow=FakeWfCfg()

step = WorkflowStep(id='flaky', name='flaky', prompt='p', retry_on_error=5, escalate_after_n_same_failures=2)
runner = WorkflowRunner(FakeCfg())
runner._current_wf = WorkflowDef(name='wf', steps=[step])
runner._current_watchdog = WorkflowWatchdog(
    paths=AgentPaths(project_root=tempfile.mkdtemp()),
    workflow_session_id='wfs_int',
    control=wf_registry.register('wfs_int'),
)

call_count = {'n': 0}
def _fake_bounded(step_, resolved_prompt, step_results):
    call_count['n'] += 1
    return StepResult(step_id=step_.id, status=StepStatus.FAILED, error='boom', error_type='TimeoutError')

with patch.object(WorkflowRunner, '_execute_step_bounded', side_effect=_fake_bounded):
    sr = runner._execute_step_with_error_retry(step, 'prompt', {})

assert sr.status == StepStatus.NEEDS_FIX
assert call_count['n'] == 2, f'阈值=2 时应只调用 2 次，实际 {call_count[\"n\"]} 次（未跑满 retry_on_error=5）'
assert '连续' in sr.error
print(f'✅ 连续 2 次同类失败后提前判 NEEDS_FIX，跳过剩余重试预算（{sr.error[:40]}...）')
"
```

**期望输出**：3 个 ✅

---



| 测试项 | 验证方式 | 通过标志 |
|--------|----------|----------|
| WorkflowDef 解析 | 单元测试一 | 5 个断言通过 |
| validate 校验（重复 id / 缺失依赖） | 单元测试一 | 正确返回错误列表 |
| Store 读写 | 单元测试二 | 保存/加载/列举/删除全部正常 |
| 拓扑排序 | 单元测试三 | 依赖顺序正确 |
| 占位符替换（output + score） | 单元测试三 | 两种占位符均正确替换 |
| 条件判断 | 单元测试三 | `>= 60` 通过，`>= 80` 不通过 |
| 条件不满足跳过步骤 | 单元测试三 | report 步骤被 SKIPPED |
| 质检门 GATE_FAILED | 单元测试四 | 低分时返回 GATE_FAILED |
| retry 重跑前序步骤 | 单元测试四 | write 被调用 2 次，含反馈 |
| retry 后通过继续执行 | 单元测试四 | publish 正常 DONE |
| list_workflows | 场景一 | 返回 code_review 信息 |
| show_workflow YAML | 场景二 | 展示完整 YAML |
| run_workflow 执行 | 场景三 | 返回完整摘要，含评分 |
| generate_workflow | 场景四 | 生成合法 YAML，通过 validate |
| save + run 链路 | 场景五 | 保存后立即可执行 |
| 手动 YAML 无重启生效 | 场景六 | 直接可用 |
| 缺参数不崩溃 | 场景七 | 无 Python 异常 |
| 文件夹模式加载（source_dir/type/role/skill_name） | 单元测试五 | 5 个断言通过 |
| prompt_file 内容读取填充 | 单元测试五 | prompt 非空且含占位符 |
| 本地 agent/skill 资源合并（WorkflowResourceBundle） | 单元测试六 | 3 个断言通过 |
| 单文件模式不构造 bundle | 单元测试六 | `build_resource_bundle` 返回 `None` |
| doc_change_review 端到端执行 | 场景八 | 4 个 step 按序完成，报告正常生成 |
| `/workflow to-dir` 迁移 | 场景九 | 迁移后目录结构、字段、校验均正确 |
| `test_workflow_step` 沙箱测试（不落盘 + mock 占位符替换） | P10 单元测试七 | 4 个断言通过 |
| `test_workflow_step` 对 human_input/require_approval 跳过 | P10 单元测试七 | 按预期提示跳过，不阻塞 |
| `resume_workflow_run(step_overrides=...)` 一次性覆盖不污染定义 | P10 单元测试八 | 持久化 YAML 前后一致 |
| `step_overrides` 非法字段/未知 step_id 被拒绝 | P10 单元测试八 | 抛出 `bad_override`，不静默忽略 |
| watchdog 连续同类失败提前升级 `NEEDS_FIX` | P10 单元测试九 | 阈值命中即短路，跳过剩余重试预算 |
| watchdog 不同 error_type 打断连续计数 | P10 单元测试九 | 计数重新从 1 开始，不误触发升级 |
