# finance-data-toolkit 优化方案

**生成时间**: 2026-08-15  
**对应阶段**: 步骤1/5 - 分析现有数据源代码，梳理解析逻辑与抓取异常点

---

## 一、核心问题诊断

### 1.1 代码结构问题（3个）

| 问题 | 严重程度 | 位置 | 影响 |
|------|----------|------|------|
| eastmoney_fetcher.py 代码结构错误 | HIGH | L356-359 | main()函数损坏，无法命令行运行 |
| eastmoney_fetcher.py 硬编码路径依赖 | HIGH | L192 | 项目结构变更导致脚本找不到 |
| 全局单例初始化时机问题 | MEDIUM | fetcher_base.py L322-326 | 循环导入时静默失败 |

### 1.2 抓取稳定性问题（3个）

| 问题 | 严重程度 | 根因 |
|------|----------|------|
| 代理池降级导致eastmoney可用率50% | HIGH | 代理质量无评分机制，无反爬应对策略 |
| akshare接口间歇性超时 | MEDIUM | 超时配置不足，fallback策略缺失 |
| 多源路由失败信息不完整 | MEDIUM | 仅保留前3个错误，调试困难 |

### 1.3 重试机制问题（1个）

| 问题 | 严重程度 | 描述 |
|------|----------|------|
| FixedIntervalPolicy不支持max_delay | MEDIUM | 测试失败，参数兼容性问题 |

### 1.4 调度器问题（2个）

| 问题 | 严重程度 | 描述 |
|------|----------|------|
| CronParser功能受限 | MEDIUM | 不支持步长/逗号/范围表达式 |
| 缺少数据freshness校验 | LOW | 无法检测缓存数据问题 |

---

## 二、优化方案

### 方案一：修复 eastmoney_fetcher.py 代码结构（P-001/P-005）

#### 2.1.1 问题描述

`eastmoney_fetcher.py` 的 `main()` 函数被截断，保存数据的代码错位到函数外，导致：
1. 脚本无法正常作为命令行工具运行
2. 路径计算通过硬编码的相对路径，脆弱且易错

#### 2.1.2 修复方案

**修复代码结构**:
```python
# 修改后：完整的 main() 函数

def main():
    parser = argparse.ArgumentParser(description='东方财富股票数据抓取工具')
    parser.add_argument('symbol', help='股票代码,如 603000')
    parser.add_argument('--headless', action='store_true', help='无头模式运行')
    parser.add_argument('-o', '--output', help='输出JSON文件路径')
    
    args = parser.parse_args()
    
    try:
        data = fetch_stock_data(args.symbol, headless=args.headless)
        
        # 打印摘要
        payload = data.payload
        print(f"\n{'='*50}")
        print(f"抓取完成: {payload.get('name', '')}({data.symbol})")
        print(f"{'='*50}")
        print(f"当前价格: ¥{payload.get('price', 'N/A')}")
        print(f"涨跌幅: {payload.get('change_pct', 'N/A')}%")
        
        # 保存文件
        output_path = Path(args.output) if args.output else \
            Path(f'./temp/{args.symbol}_eastmoney_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        print(f"\n数据已保存至: {output_path}")
        
    except Exception as e:
        print(f"抓取失败: {e}", file=sys.stderr)
        sys.exit(1)
```

**修复路径依赖**:
```python
# 修改后：使用环境变量或配置指定 browser-cdp 路径

def get_browser_cdp_path():
    """获取 browser-cdp 脚本路径"""
    # 优先从环境变量读取
    env_path = os.environ.get('BROWSER_CDP_PATH')
    if env_path:
        return Path(env_path)
    
    # 其次从配置文件读取
    config_path = Path(__file__).parent.parent / 'config' / 'paths.json'
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return Path(json.load(f).get('browser_cdp', ''))
    
    # 最后尝试默认路径（向后兼容）
    return Path(__file__).parent.parent.parent.parent / 'browser-cdp'
```

---

### 方案二：修复全局单例初始化问题（P-002）

#### 2.2.1 问题描述

`fetcher_base.py` 模块级初始化在循环导入时静默失败，导致后续 `get_fetcher()` 返回 None。

#### 2.2.2 修复方案

```python
# 修改后：移除模块级初始化，改为懒加载

_global_router = None


def get_global_router() -> MultiSourceFetcher:
    """获取全局路由器（懒加载）"""
    global _global_router
    if _global_router is None:
        try:
            _global_router = create_default_router()
            logger.info("全局路由器已初始化")
        except Exception as e:
            logger.error(f"全局路由器初始化失败: {e}")
            raise
    return _global_router


def get_fetcher(source: str) -> Optional[BaseFetcher]:
    """根据名称获取已注册的抓取器"""
    try:
        router = get_global_router()
        return router._registered_fetchers.get(source)
    except Exception as e:
        logger.warning(f"获取抓取器 {source} 失败: {e}")
        return None
```

---

### 方案三：优化多源路由失败信息（F-003）

#### 2.3.1 问题描述

当前只保留前3个错误，调试困难。

#### 2.3.2 修复方案

```python
# 修改后：保留全部错误但限制总长度，增加结构化报告

def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[FinanceData]:
    ...
    errors = []
    results = []
    
    for src in sources:
        try:
            # 执行抓取...
            pass
        except Exception as e:
            err_info = {
                'source': src,
                'error_type': type(e).__name__,
                'message': str(e)[:200],  # 限制长度
                'timestamp': datetime.now().isoformat(),
            }
            errors.append(err_info)
            logger.warning(f"数据源 {src} 失败: {e}")
            if src in self._circuit_breakers:
                self._circuit_breakers[src]._on_failure()
    
    if not results:
        from ..exceptions import FallbackError
        # 构建结构化错误报告
        error_report = {
            'data_type': data_type,
            'symbols': symbols,
            'total_sources': len(sources),
            'failed_sources': len(errors),
            'errors': errors,
        }
        error_msg = (
            f"所有数据源均失败 [{data_type}] "
            f"({len(errors)}/{len(sources)} 个数据源失败)\n"
            f"错误详情: {json.dumps(error_report, ensure_ascii=False, indent=2)}"
        )
        raise FallbackError(error_msg)
```

---

### 方案四：统一重试策略参数接口（F-004）

#### 2.4.1 问题描述

`FixedIntervalRetry` 不支持 `max_delay` 参数，导致测试失败。

#### 2.4.2 修复方案

```python
# 修改后：所有重试策略统一接受 max_delay 参数

class FixedIntervalRetry(RetryStrategy):
    """
    固定间隔重试策略
    
    支持 max_delay 参数（虽然固定间隔不适用，但保持接口一致）
    """
    
    def __init__(self, config: RetryConfig):
        self.config = config
        # 忽略 max_delay，保持接口一致
        
    def get_delay(self, attempt: int) -> float:
        return min(self.config.base_delay, self.config.max_delay)
```

---

### 方案五：增强 CronParser 功能（P-004）

#### 2.5.1 问题描述

自定义 CronParser 功能受限，不支持步长/逗号/范围表达式。

#### 2.5.2 修复方案

```python
class CronParser:
    """简易 cron 表达式解析器（增强版）"""
    
    @staticmethod
    def parse(cron_expr: str) -> Dict[str, Any]:
        """解析 cron 表达式，支持 */N, 1,2,3, 1-5 等表达式"""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}")
        
        return {
            'minute': CronParser._parse_field(parts[0], 0, 59),
            'hour': CronParser._parse_field(parts[1], 0, 23),
            'day': CronParser._parse_field(parts[2], 1, 31),
            'month': CronParser._parse_field(parts[3], 1, 12),
            'weekday': CronParser._parse_field(parts[4], 0, 6),
        }
    
    @staticmethod
    def _parse_field(field: str, min_val: int, max_val: int) -> Union[int, Set[int]]:
        """解析单个字段，返回 int 或 set"""
        # 通配符
        if field == '*':
            return -1  # 表示全部
        
        # 步长表达式 */N
        if field.startswith('*/'):
            step = int(field[2:])
            return {'step', step, min_val, max_val}
        
        # 逗号表达式 1,2,3
        if ',' in field:
            return {int(x) for x in field.split(',')}
        
        # 范围表达式 1-5
        if '-' in field:
            start, end = field.split('-')
            return set(range(int(start), int(end) + 1))
        
        # 普通数字
        return int(field)
    
    @staticmethod
    def matches(expr: Dict[str, Any], dt: datetime) -> bool:
        """检查给定时间是否匹配 cron 表达式"""
        def check_field(value: Union[int, Set[int]], actual: int) -> bool:
            if value == -1:  # 通配符
                return True
            if isinstance(value, set):
                if 'step' in value:
                    _, step, min_val, max_val = value
                    return (actual - min_val) % step == 0
                return actual in value
            return value == actual
        
        return all([
            check_field(expr['minute'], dt.minute),
            check_field(expr['hour'], dt.hour),
            check_field(expr['day'], dt.day),
            check_field(expr['month'], dt.month),
            check_field(expr['weekday'], dt.weekday()),
        ])
```

---

### 方案六：增加数据 freshness 校验（F-005）

#### 2.6.1 问题描述

调度器执行完成后，无法验证数据时效性。

#### 2.6.2 修复方案

在 `quality_monitor.py` 中增加 freshness 检查规则：

```python
class FreshnessChecker:
    """数据新鲜度检查器"""
    
    # 各数据类型允许的最大延迟（分钟）
    MAX_DELAY_MINUTES = {
        'quote': 30,           # 行情数据：30分钟
        'kline': 60,           # K线数据：1小时
        'financial': 1440,     # 财务数据：24小时
        'news': 60,            # 新闻数据：1小时
        'sentiment': 120,      # 情绪数据：2小时
        'sector': 60,          # 板块数据：1小时
    }
    
    def check_freshness(self, data: List[FinanceData], data_type: str) -> List[QualityIssue]:
        """检查数据新鲜度"""
        issues = []
        max_delay = self.MAX_DELAY_MINUTES.get(data_type, 60)
        
        for item in data:
            try:
                ts = datetime.fromisoformat(item.timestamp)
                age_minutes = (datetime.now() - ts).total_seconds() / 60
                
                if age_minutes > max_delay:
                    issues.append(QualityIssue(
                        issue_id=f"freshness_{item.symbol}",
                        severity='warning',
                        category='timeliness',
                        source=item.source,
                        message=f"数据过期: {age_minutes:.1f}分钟 > {max_delay}分钟",
                        details={'age_minutes': age_minutes, 'max_delay': max_delay},
                    ))
            except (ValueError, TypeError):
                issues.append(QualityIssue(
                    issue_id=f"freshness_invalid_{item.symbol}",
                    severity='critical',
                    category='completeness',
                    source=item.source,
                    message="时间戳格式无效",
                ))
        
        return issues
```

---

## 三、实施计划

### 本轮（步骤2/5）：稳定性修复

| 任务 | 文件 | 预计工时 |
|------|------|----------|
| 修复 eastmoney_fetcher.py 代码结构 | `data_fetching/eastmoney_fetcher.py` | 30min |
| 修复路径依赖 | `data_fetching/eastmoney_fetcher.py` | 20min |
| 修复全局单例初始化 | `data_fetching/fetcher_base.py` | 20min |
| 删除孤立代码 | `scheduler.py` | 5min |
| 优化失败聚合信息 | `data_fetching/fetcher_base.py` | 30min |
| 统一重试策略参数 | `retry_strategy.py` | 20min |

**合计**: ~2.5小时

### 下轮（步骤3/5）：解析能力增强

| 任务 | 描述 |
|------|------|
| 实现 DataParser 抽象层 | 新建 `data_parsing/parser.py` |
| 解耦解析逻辑 | 重构各 fetcher 的解析代码 |
| 增加 eastmoney 专用配置 | 更新 `data_source_config.py` |
| 补充缺失数据类型适配 | 新增 10+ 数据类型 |

### 后续轮次

- 步骤4/5: 抓取稳定性优化（代理池质量评分、akshare超时配置等）
- 步骤5/5: 定时调度与错误恢复（CronParser增强、动态并发控制等）

---

## 四、预期效果

### 4.1 稳定性指标

| 指标 | 当前 | 目标 |
|------|------|------|
| eastmoney 可用率 | 50% | 85%+ |
| akshare 超时率 | 15% | <5% |
| 多源路由成功率 | 67% | 90%+ |

### 4.2 代码质量指标

| 指标 | 当前 | 目标 |
|------|------|------|
| 关键bug数量 | 3 | 0 |
| 中危问题数量 | 6 | 2 |
| 低危问题数量 | 2 | 0 |

### 4.3 可维护性指标

- 解析逻辑与抓取逻辑解耦
- 重试策略参数接口统一
- CronParser 功能完整
- 数据 freshness 校验自动化

---

## 五、风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| browser-cdp 脚本路径变更 | 中 | 高 | 使用环境变量+配置文件双保险 |
| akshare API变更 | 高 | 中 | 增加fallback策略 |
| eastmoney反爬升级 | 中 | 高 | 代理池质量监控+自动切换 |
| 测试用例不兼容 | 低 | 中 | 保留向后兼容接口 |

---

## 六、验收标准

### 6.1 功能验收

- [ ] eastmoney_fetcher.py 命令行模式正常运行
- [ ] 多源路由失败时输出完整错误报告
- [ ] 重试策略支持 max_delay 参数
- [ ] CronParser 支持 */N 步长表达式
- [ ] 数据 freshness 检查正常工作

### 6.2 性能验收

- [ ] 路由失败聚合信息生成时间 < 100ms
- [ ] CronParser 解析时间 < 10ms
- [ ] 单条数据 freshness 检查时间 < 1ms

### 6.3 测试验收

- [ ] 所有现有测试通过
- [ ] 新增测试用例覆盖率 > 80%
- [ ] 端到端集成测试通过

---

**文档版本**: v1.0  
**最后更新**: 2026-08-15
