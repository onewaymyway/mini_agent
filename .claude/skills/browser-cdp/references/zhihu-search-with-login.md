# 知乎登录态搜索器参考文档

## 概述

`zhihu_search_with_login.py` 是一个使用已登录浏览器会话搜索知乎真实问题的工具脚本。与普通的 `zhihu_search.py` 不同，此脚本需要预先启动一个已登录知乎的浏览器实例，通过 CDP 协议直接操作浏览器进行搜索和结果提取。

## 前置条件

1. **已登录浏览器**：必须先运行 `launch_zhihu_logged_in.py` 启动一个已登录知乎的 Chrome 浏览器实例
2. **CDP 端口**：默认使用端口 `9336`，需确保浏览器以调试模式启动
3. **WebSocket 依赖**：需要 `websocket-client` 库

## 使用方式

### 单次搜索

```bash
python zhihu_search_with_login.py "搜索关键词"
```

### 批量搜索（15个 Agent 方向）

```bash
python zhihu_search_with_login.py --batch
```

### 自定义关键词文件

```bash
python zhihu_search_with_login.py --keywords-file keywords.json
```

关键词文件格式（JSON 数组）：
```json
["关键词1", "关键词2", "关键词3"]
```

### 自定义参数

```bash
python zhihu_search_with_login.py "关键词" \
  --port 9336 \
  --min-results 30 \
  --max-results 60 \
  --max-scrolls 12 \
  --scroll-pause 3 \
  --output results.json
```

## 核心参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | 9336 | CDP 调试端口 |
| `--min-results` | 30 | 每个关键词至少获取的结果数 |
| `--max-results` | 60 | 每个关键词最终保留的结果上限 |
| `--max-scrolls` | 12 | 最多滚动加载次数 |
| `--scroll-pause` | 3 | 每次滚动后等待秒数 |
| `--output` | zhihu_real_questions.json | 输出文件路径 |
| `--keywords-file` | - | 自定义关键词文件路径 |
| `--batch` | - | 批量搜索预设的 15 个 Agent 方向 |

## 工作原理

1. **连接验证**：检查指定端口的浏览器是否运行且包含知乎 tab
2. **导航搜索**：通过 CDP `Page.navigate` 命令导航到知乎搜索页
3. **智能滚动**：
   - 首屏提取结果
   - 若结果数不足 `min_results`，向下滚动加载更多内容
   - 每次滚动后等待 `scroll_pause` 秒让内容加载
   - 若连续滚动无新结果则提前停止
4. **结果提取**：通过 JavaScript 执行提取问题链接和标题
5. **去重输出**：按 href 去重，保留首次出现顺序

## 输出格式

```json
{
  "questions": [
    {
      "id": "q1",
      "title": "问题标题",
      "url": "https://www.zhihu.com/question/xxxxx",
      "snippet": "",
      "matched_keywords": ["搜索关键词"],
      "search_page_meta": {}
    }
  ],
  "total_keywords_searched": 15,
  "total_unique_questions": 120
}
```

## 预设搜索关键词（--batch 模式）

| 关键词 | 内容 ID | 方向 |
|--------|---------|------|
| 影视推荐工具 | agent_topic_001 | 个性化影视内容发现与决策 Agent |
| 如何选电影 | agent_topic_001 | 个性化影视内容发现与决策 Agent |
| 追剧进度管理 | agent_topic_002 | 追剧进度管理与剧集深度解读 Agent |
| 控制刷短视频时间 | agent_topic_003 | 短视频/直播内容智能策展 Agent |
| 游戏攻略工具 | agent_topic_004 | 游戏攻略自动生成与实时辅助 Agent |
| 游戏陪玩平台 | agent_topic_005 | 游戏陪玩/陪练/代练智能 Agent |
| 游戏账号管理 | agent_topic_006 | 游戏账号资产管理与交易辅助 Agent |
| 社交媒体管理工具 | agent_topic_007 | 社交媒体内容智能策展 Agent |
| 社群运营方法 | agent_topic_008 | 兴趣圈层深度运营与社群裂变 Agent |
| 新番追踪工具 | agent_topic_009 | 二次元/ACG 内容多源聚合 Agent |
| 比价工具 | agent_topic_010 | 全网比价与智能购物决策 Agent |
| 探店 APP 推荐 | agent_topic_011 | 本地生活探店/团购/预约全流程 Agent |
| 旅行规划工具 | agent_topic_012 | 旅行规划预订与行程执行 Agent |
| 碎片化学习 | agent_topic_013 | 碎片化学习路径规划与知识内化 Agent |
| 自学技能反馈 | agent_topic_014 | 技能练习陪伴与实时反馈 Agent |
| 如何养成习惯 | agent_topic_015 | 习惯养成闭环与行为设计 Agent |

## 依赖模块

- `src.core.browser_console` - CDP 会话管理
- `src.core.browser_nav` - 导航命令
- `websocket-client` - WebSocket 连接
- `urllib` - HTTP 请求（CDP API 调用）

## 注意事项

1. **登录态保持**：浏览器会话需保持登录状态，过期需重新登录
2. **风控限制**：滚动次数和频率受知乎风控限制，建议不要过度调整参数
3. **端口冲突**：确保端口 9336 未被其他进程占用
4. **结果质量**：搜索结果依赖知乎搜索算法，可能包含广告或低质量内容

## 相关文件

- `launch_zhihu_logged_in.py` - 启动已登录浏览器
- `run_real_search_with_logged_in_browser.py` - 集成搜索流程
- `zhihu_search.py` - 普通搜索器（无需登录）
- `zhihu_search_simple.py` - 简化版搜索器

## 版本历史

- v1.0: 初始版本，支持单次和批量搜索
- 智能滚动加载策略，自动检测结果上限
- 去重输出，保持结果顺序
