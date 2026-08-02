# Finance Data Toolkit 示例代码

本目录包含 finance_data_toolkit 各模块的使用示例。

## 示例列表

### 1. 数据质量校验示例

**文件**: `data_validation_example.py`

演示如何使用数据质量校验模块进行 K 线数据和实时行情的完整性检查与异常值检测。

**运行方式**:
```bash
cd .claude/skills/finance-data-toolkit
python examples/data_validation_example.py
```

**功能**:
- 基础数据验证（K 线/行情）
- 使用 DataQualityValidator 类进行自定义验证
- 自动数据类型检测
- 自定义检查参数（宽松/严格模式）
- 错误处理示例

**输出示例**:
```
============================================================
示例 1: 基础数据验证
============================================================

1. 验证正常 K 线数据:
质量报告:
  状态：✓ 通过
  问题数：0
  严重问题：0
  警告：0

2. 验证有问题 K 线数据:
质量报告:
  状态：✗ 失败
  问题数：4
  严重问题：2
  警告：2
  
  问题详情:
  - [严重] 空值检测：发现 1 个空值 (close@10)
  - [严重] 价格逻辑检查：high < low (20)
  - [警告] 异常值检测：close 异常 (30, Z-score=4.2)
  - [警告] 负值检查：volume 为负 (40)
```

## 更多示例

后续将添加更多示例：
- `async_fetch_example.py`: 异步数据获取示例
- `exception_handling_example.py`: 异常处理与降级策略示例
- `batch_processing_example.py`: 批量数据处理示例
- `technical_analysis_example.py`: 技术指标计算示例
- `sentiment_analysis_example.py`: 舆情分析示例

## 运行所有示例

```bash
# 运行单个示例
python examples/data_validation_example.py

# 运行所有示例（待实现）
python examples/run_all.py
```

## 注意事项

1. 确保已安装所有依赖：
   ```bash
   pip install pandas numpy httpx
   ```

2. 示例数据为模拟数据，不会调用真实 API

3. 如需测试真实数据，请修改示例代码中的数据源配置

## 贡献

欢迎提交新的示例代码！请遵循以下规范：

1. 文件名：`<module_name>_example.py`
2. 包含完整的 docstring 说明
3. 提供清晰的运行示例
4. 添加必要的注释
