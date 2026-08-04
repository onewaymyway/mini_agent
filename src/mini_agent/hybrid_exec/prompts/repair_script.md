你是一名 Python 工程师，下面这个脚本在执行任务时报错了，请修复它。

## 任务描述
{description}

## 本次输入数据样例（JSON）
{input_sample}

## 出错的脚本源码
```python
{broken_code}
```

## 报错信息
错误类型：{error_type}
错误信息：{error_message}
Traceback：
{traceback}

## 要求
1. 保持脚本的整体设计思路不变，只修复导致这次报错的问题；除非原脚本的设计本身就有问题（比如完全没处理某类输入），才允许做更大的调整。
2. 脚本协议不变（仍必须定义 `run(ctx)` 入口，`ctx` 的能力、返回值要求与之前一致）。
3. 只输出修复后的完整脚本源码，不要输出任何解释文字，不要用 markdown 代码块包裹。
