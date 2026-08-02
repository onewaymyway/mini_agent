"""使用 browser-cdp 通过真实浏览器搜索知乎问题"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

# 添加 publish-system 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude" / "skills" / "publish-system"))

from content_library.models import ContentItem, ContentCategory, ContentStatus, ContentMetadata
from content_library.repository import ContentRepository

# 报告中的 15 个应用方向
AGENT_TOPICS = [
    {
        "title": "个性化影视内容发现与决策 Agent",
        "content": "用户面对 Netflix/爱奇艺/腾讯视频等平台海量片单，花费大量时间浏览预告片、查豆瓣评分、看知乎推荐，最终仍可能选到不满意的影片。痛点：信息过载（单平台 5000+ 影片）、评价体系碎片化（豆瓣/IMDb/烂番茄标准不一）、个性化缺失（推荐命中率<15%）、版权分散导致可看性不确定。Agent 角色：私人影视总监，理解用户口味画像、情境需求、观看设备、订阅会员，输出'今晚看什么'的唯一最优解。核心能力：多源聚合（豆瓣/IMDb/各平台 API）+ LLM 理解推理（用户自然语言需求如'今晚想看轻松治愈的日剧'）+ 工具调用（查询各平台版权）+ 记忆个性化（观看历史、评分偏好）。",
        "tags": ["影视推荐", "内容发现", "决策辅助", "个性化推荐", "Agent 应用"],
        "search_queries": ["影视推荐工具", "如何选电影", "豆瓣评分可信吗"]
    },
    {
        "title": "追剧进度管理与剧集深度解读 Agent",
        "content": "追剧用户痛点：多剧并行容易忘记看到哪里、错过重要细节、看不懂隐喻彩蛋、跟不上社群讨论节奏。Agent 能力：自动记录每部剧的观看进度（集数/时间点）、提供剧情回顾和人物关系图谱、解析隐喻彩蛋和文化背景、筛选同进度同口味的讨论、生成个性化观剧报告。技术实现：浏览器扩展/APP 集成 + 数据库记录 + LLM 剧情分析 + 社群数据聚合。",
        "tags": ["追剧管理", "剧情解读", "进度追踪", "社群讨论", "娱乐 Agent"],
        "search_queries": ["追剧进度管理", "追剧 APP 推荐", "如何记录看剧进度"]
    },
    {
        "title": "短视频/直播内容智能策展与信息饮食管理 Agent",
        "content": "短视频/直播用户痛点：算法推荐导致信息茧房、无意识刷屏浪费时间、优质内容被淹没、无法系统性获取知识。Agent 方案：用户设定'信息饮食计划'（如'每天 30 分钟科技资讯+15 分钟英语学习'）+ Agent 跨平台聚合（抖音/B 站/快手/视频号）+ 智能过滤低质内容 + 定时提醒与时长控制 + 生成学习总结。价值：从被动消费转向主动获取，平衡娱乐与学习。",
        "tags": ["短视频", "直播", "内容策展", "时间管理", "信息饮食"],
        "search_queries": ["控制刷短视频时间", "短视频信息茧房", "抖音知识获取"]
    },
    {
        "title": "游戏攻略自动生成与实时辅助 Agent",
        "content": "游戏玩家痛点：攻略搜索耗时、图文攻略阅读困难、实时问题无法解决、个性化 Build 难构建。Agent 能力：基于游戏知识库自动生成图文/视频攻略、游戏内实时辅助（OCR 识别+LLM 推理 + 语音/文字提示）、Build 计算器与模拟器、成就/收集品追踪。技术栈：游戏数据爬虫 + 知识图谱 + LLM 生成 + OCR + 游戏内覆盖层。",
        "tags": ["游戏攻略", "实时辅助", "游戏 Agent", "Build 构建", "成就追踪"],
        "search_queries": ["游戏攻略工具", "AI 辅助游戏", "游戏 Build 构建"]
    },
    {
        "title": "游戏陪玩/陪练/代练智能 Agent",
        "content": "游戏社交痛点：找不到水平相当/性格合拍的队友、真人陪玩价格高且不稳定、时间协调困难。Agent 方案：AI 陪玩（语音交互 + 游戏操作 + 情绪陪伴）、智能匹配真人队友、自动预约与日程管理、训练模式与实时反馈。技术实现：游戏 AI（基于强化学习）+ 语音合成/识别 + 匹配算法 + 支付系统。商业模式：按小时计费、会员制、赛事陪练。",
        "tags": ["游戏陪玩", "AI 队友", "游戏社交", "智能匹配", "虚拟陪练"],
        "search_queries": ["游戏陪玩平台", "AI 游戏陪玩", "游戏队友匹配"]
    },
    {
        "title": "游戏账号资产管理与交易辅助 Agent",
        "content": "游戏玩家资产管理痛点：多账号多平台难以统一管理、装备/角色/货币价值不透明、交易风险高、市场趋势难把握。Agent 能力：多账号统一仪表盘、实时估值（基于市场数据 + 稀有度 + 需求）、交易风险检测（诈骗/黑产）、市场趋势分析、最佳出售时机建议。技术：游戏 API 集成 + 市场数据爬取 + 估值模型 + 风控算法。",
        "tags": ["游戏资产", "账号管理", "交易辅助", "价值评估", "风险管理"],
        "search_queries": ["游戏账号管理", "游戏装备估值", "游戏交易平台"]
    },
    {
        "title": "社交媒体内容智能策展与信息饮食管理 Agent",
        "content": "社交媒体用户痛点：信息过载（微博/知乎/小红书/Twitter 多平台）、信息茧房（算法只推喜欢的内容）、低质内容泛滥、重要信息被淹没、时间被无意义消耗。Agent 方案：跨平台聚合 + 用户设定信息优先级 + 智能去重与摘要 + 质量评分过滤 + 定时推送 + 使用时长控制。价值：从被动刷帖转向主动获取高质量信息，重建'信息饮食健康'。",
        "tags": ["社交媒体", "内容策展", "信息聚合", "质量过滤", "时间管理"],
        "search_queries": ["社交媒体管理工具", "信息过载怎么办", "多平台信息聚合"]
    },
    {
        "title": "兴趣圈层深度运营与社群裂变 Agent",
        "content": "社群运营者痛点：内容生产压力大、用户活跃度低、裂变增长难、商业化转化低。Agent 能力：自动生成圈层内容（图文/视频/话题）、智能互动（回复评论/发起讨论）、用户画像与分层运营、裂变活动设计与执行、商业化内容植入。技术：内容生成（LLM+ 图像生成）+ 用户行为分析 + 社群管理工具集成 + A/B 测试。",
        "tags": ["社群运营", "内容生成", "用户增长", "裂变营销", "圈层经济"],
        "search_queries": ["社群运营方法", "社群裂变", "AI 社群运营"]
    },
    {
        "title": "二次元/ACG 内容多源聚合与个性化推送 Agent",
        "content": "二次元用户痛点：动漫/漫画/游戏/周边信息分散（B 站/贴吧/微博/豆瓣/专门网站）、新番/新刊信息遗漏、同好交流困难、代购/海淘信息不对称。Agent 方案：多源聚合（新番表/新刊发布/活动资讯）+ 个性化推荐（基于收藏/评分/浏览历史）+ 同好匹配 + 代购信息整合 + 收藏管理。技术：多站点爬虫 + 推荐算法 + 社交图谱 + 电商 API。",
        "tags": ["二次元", "ACG", "内容聚合", "新番推荐", "同好社交"],
        "search_queries": ["新番追踪工具", "ACG 资讯", "二次元同好交流"]
    },
    {
        "title": "全网比价与智能购物决策 Agent",
        "content": "购物者痛点：同一商品多平台价格差异大、优惠券规则复杂、历史价格不透明、假货风险、促销时机难把握。Agent 能力：多平台实时比价（淘宝/京东/拼多多/抖音/亚马逊）、优惠券自动计算、历史价格追踪与趋势预测、假货风险检测、最佳购买时机建议、价格提醒。技术：电商 API/爬虫 + 价格数据库 + 规则引擎 + 机器学习预测。",
        "tags": ["比价", "购物决策", "优惠券", "价格追踪", "消费 Agent"],
        "search_queries": ["比价工具", "网购降价查询", "双 11 什么时候买"]
    },
    {
        "title": "本地生活探店/团购/预约全流程 Agent",
        "content": "本地生活痛点：餐厅/娱乐/服务信息分散（大众点评/美团/抖音/小红书）、真实评价难辨别、团购套餐不划算、预约排队耗时。Agent 方案：多源信息聚合 + 真实评价筛选（去水军）+ 个性化推荐（基于口味/预算/位置）+ 团购套餐优化计算 + 自动预约与排队 + 消费后评价生成。技术：本地生活 API + NLP 评价分析 + 推荐系统 + 预约系统集成。",
        "tags": ["本地生活", "探店", "团购", "预约", "消费决策"],
        "search_queries": ["探店 APP 推荐", "大众点评真实评价", "团购套餐划算吗"]
    },
    {
        "title": "旅行规划预订与行程执行 Agent",
        "content": "旅行者痛点：行程规划耗时（机票/酒店/景点/餐饮需分别查询）、信息不一致、突发情况应对困难、当地信息获取难、分享整理麻烦。Agent 能力：一键生成完整行程（基于预算/时间/兴趣偏好）、自动比价预订、实时行程调整（天气/交通/排队情况）、当地攻略与语言翻译、自动生成旅行游记。技术：旅游 API 聚合 + LLM 行程规划 + 实时数据监控 + 地图服务 + 翻译服务。",
        "tags": ["旅行规划", "行程管理", "自动预订", "旅行助手", "智能导游"],
        "search_queries": ["旅行规划工具", "如何规划旅行", "机票酒店什么时候订"]
    },
    {
        "title": "碎片化学习路径规划与知识内化 Agent",
        "content": "学习者痛点：想学但不知从何开始、学习资源太多难以选择、碎片时间利用率低、学完容易忘记、缺乏实践反馈。Agent 方案：基于目标生成个性化学习路径（视频/文章/书籍/课程）+ 碎片时间适配（5 分钟/15 分钟/30 分钟模块）+ 间隔重复记忆系统 + 实践项目推荐 + 学习进度追踪与调整。技术：知识图谱 + 资源爬取与分类 + 学习科学算法 + 进度追踪。",
        "tags": ["碎片化学习", "学习规划", "知识内化", "记忆系统", "个人成长"],
        "search_queries": ["碎片化学习", "学习规划工具", "如何记住知识"]
    },
    {
        "title": "技能练习陪伴与实时反馈 Agent",
        "content": "技能学习者痛点：自学缺乏指导、练习过程无人纠正、进步缓慢难以坚持、反馈不及时。Agent 能力：实时语音/视频反馈（如口语练习/乐器练习/健身动作）、错误检测与纠正建议、个性化练习计划、进步可视化、虚拟对手/伙伴。技术：语音识别/合成 + 计算机视觉 + 强化学习 + 游戏化设计。适用场景：语言学习/乐器/健身/编程/绘画等。",
        "tags": ["技能练习", "实时反馈", "学习陪伴", "错误纠正", "虚拟教练"],
        "search_queries": ["自学技能反馈", "语言学习 APP", "AI 私人教练"]
    },
    {
        "title": "习惯养成闭环与行为设计 Agent",
        "content": "习惯养成痛点：目标设定不合理、缺乏持续动力、容易半途而废、无法量化进步、环境因素干扰。Agent 方案：基于行为设计学设定可执行目标 + 微习惯拆解 + 触发机制设计 + 即时奖励系统 + 社交监督 + 失败分析与调整。技术：行为心理学模型 + 习惯追踪 + 推送通知 + 社交功能 + 数据分析。价值：将'意志力依赖'转为'系统设计'，提高习惯养成成功率。",
        "tags": ["习惯养成", "行为设计", "自我管理", "目标达成", "个人效能"],
        "search_queries": ["如何养成习惯", "习惯追踪 APP", "如何坚持做一件事"]
    }
]


def create_content_items():
    """创建内容项列表"""
    items = []
    for i, topic in enumerate(AGENT_TOPICS, 1):
        item = ContentItem(
            id=f"agent_topic_{i:03d}",
            title=topic["title"],
            content=topic["content"],
            tags=topic["tags"],
            category=ContentCategory.ARTICLE,
            status=ContentStatus.READY,
            metadata=ContentMetadata(
                author="mini_agent 自动化分析系统",
                source="daily_life_entertainment_agent_report.md",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                word_count=len(topic["content"]),
                extra={"report_version": "v1.0"}
            )
        )
        items.append(item)
    return items


def save_to_repository(items, repo_path):
    """保存内容到仓库"""
    repo = ContentRepository(repo_path)
    for item in items:
        repo.save(item)
        print(f"已保存：{item.id} - {item.title}")
    print(f"\n共保存 {len(items)} 条内容到 {repo_path}")


def search_zhihu_with_browser(query, max_results=5):
    """使用 browser-cdp 搜索知乎问题"""
    print(f"  搜索：{query}")
    
    # 构建知乎搜索 URL
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.zhihu.com/search?type=question&q={encoded_query}"
    
    # 这里需要调用 browser-cdp skill 来执行搜索
    # 由于无法直接在脚本中调用 skill，我们返回一个占位符
    # 实际使用时需要手动调用 browser-cdp
    
    return {
        "query": query,
        "url": search_url,
        "status": "pending_browser_search",
        "message": f"请使用 browser-cdp 访问：{search_url}"
    }


def main():
    # 配置路径
    skill_dir = Path(__file__).parent.parent.parent / ".claude" / "skills" / "publish-system"
    content_path = skill_dir / "content_library" / "content"
    
    # 创建内容目录
    content_path.mkdir(parents=True, exist_ok=True)
    
    print("="*80)
    print("Publish-System 测试：使用 browser-cdp 搜索知乎问题")
    print("="*80)
    
    # 1. 创建内容项
    print("\n[步骤 1] 创建内容项...")
    items = create_content_items()
    print(f"创建了 {len(items)} 条内容")
    
    # 2. 保存到仓库
    print("\n[步骤 2] 保存到内容仓库...")
    save_to_repository(items, str(content_path))
    
    # 3. 生成搜索任务列表
    print("\n[步骤 3] 生成知乎搜索任务列表...")
    print("\n请使用 browser-cdp 依次访问以下链接进行搜索：\n")
    
    search_tasks = []
    for i, topic in enumerate(AGENT_TOPICS, 1):
        print(f"\n【{i:2d}. {topic['title']}】")
        for j, query in enumerate(topic["search_queries"], 1):
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            search_url = f"https://www.zhihu.com/search?type=question&q={encoded_query}"
            search_tasks.append({
                "content_id": f"agent_topic_{i:03d}",
                "content_title": topic["title"],
                "tags": topic["tags"],
                "query": query,
                "url": search_url
            })
            print(f"  {j}. {query}")
            print(f"     {search_url}")
    
    # 保存搜索任务列表
    tasks_file = Path(__file__).parent / "zhihu_search_tasks.json"
    with open(tasks_file, 'w', encoding='utf-8') as f:
        json.dump(search_tasks, f, ensure_ascii=False, indent=2)
    
    print(f"\n搜索任务列表已保存到：{tasks_file}")
    print(f"\n共 {len(search_tasks)} 个搜索任务")
    print("\n下一步：")
    print("1. 使用 browser-cdp 打开知乎搜索页面")
    print("2. 依次访问每个搜索 URL，手动复制问题列表")
    print("3. 将结果保存后，可以使用匹配算法进行排序")


if __name__ == "__main__":
    main()
