# 工作流系统测试说明

## 功能概述

测试 mini_agent 工作流系统的完整功能，验证：
- YAML 定义的解析与保存（WorkflowDef / WorkflowStore）
- 步骤执行引擎：拓扑排序、占位符替换、条件判断（WorkflowRunner）
- 质检门：evaluator 角色绑定、评分提取、GATE_FAILED 状态、retry 重跑
- LLM 自动生成工作流（WorkflowGenerator）
- 6 个内置工具在主 Agent 对话中的完整使用流程

---

## 前置条件

1. 已完成 workflow 模块安装（`src/mini_agent/workflow/` 目录存在）
2. `pyyaml` 已安装：`pip install pyyaml --break-system-packages`
3. 示例工作流存在：`.agent/workflows/code_review.yaml`
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

## 验证 Checklist

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
