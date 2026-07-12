#!/usr/bin/env python
"""
详情页专用清理规则模块

针对不同网站（CSDN、知乎、百家号、GitHub 等）提供定向的内容清理规则。
"""

import re
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse


# ========== 通用清理规则 ==========

def clean_generic(text: str, max_chars: int = 5000, max_lines: int = 200) -> str:
    """通用清理：移除 CSS/JS 代码、空行、过短行"""
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        # 跳过明显的 CSS/JS 代码行
        if (line.startswith('.') and '{' in line) or \
           (line.startswith('@') and '{' in line) or \
           line.startswith('function') or \
           line.startswith('var ') or \
           line.startswith('const ') or \
           line.startswith('let ') or \
           line.startswith('import ') or \
           line.startswith('export ') or \
           line.startswith('require(') or \
           line.startswith('module.exports'):
            continue
        cleaned_lines.append(line)
    
    cleaned_text = '\n'.join(cleaned_lines[:max_lines])
    return cleaned_text[:max_chars]


# ========== 站点特定清理规则 ==========

def clean_csdn(text: str, max_chars: int = 5000) -> str:
    """CSDN 博客清理规则"""
    # 移除常见的 CSDN 干扰内容
    patterns_to_remove = [
        r'\b(版权声明|本文为博主原创文章|遵循.*?协议|转载请附上原文出处链接|博主专栏|更多文章|相关推荐|热门文章|最新文章|阅读全文|展开阅读|收起全文|点赞|收藏|分享|评论|关注|私信|举报|违规|版权|原力计划|博客专家|CSDN认证博客专家|CSDN认证企业博客|版权归作者所有|商业转载请联系作者获得授权|非商业转载请注明出处).*',
        r'\b(登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|APP下载|客户端|移动端|PC端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|外包服务|技术支持|客服|QQ群|微信群|交流群|技术交流).*',
        r'^\s*[\d\.]+\s*$',  # 纯数字行（如行号）
        r'^\s*[\-=]{3,}\s*$',  # 分隔线
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    # 移除多余空行
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_zhihu(text: str, max_chars: int = 5000) -> str:
    """知乎清理规则"""
    patterns_to_remove = [
        r'\b(知乎|Zhihu|盐选|会员|专栏|圆桌|想法|视频|直播|发现|等你来答|关注问题|邀请回答|收藏|赞同|感谢|分享|评论|发布于|编辑于|更新于|版权声明|禁止转载|授权转载|知识共享|署名-非商业性使用-禁止演绎|CC BY-NC-ND|原创|首发|独家|独家首发).*',
        r'\b(登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|知乎App|知乎客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
        r'\b(展开|收起|查看更多|加载更多|继续阅读|阅读全文|全文|摘要|目录|章节|段落|标题|作者|时间|来源|编辑|责编|审核|发布|更新).*',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_baijiahao(text: str, max_chars: int = 5000) -> str:
    """百家号清理规则"""
    patterns_to_remove = [
        r'\b(百家号|百度|版权声明|原创|首发|独家|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|百度App|百度客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_github(text: str, max_chars: int = 5000) -> str:
    """GitHub 页面清理规则"""
    patterns_to_remove = [
        r'\b(GitHub|Sign in|Sign up|Pricing|Search|Explore|Marketplace|Topics|Collections|Trending|Learning Lab|Documentation|GitHub Sponsors|GitHub Actions|GitHub Codespaces|GitHub Copilot|GitHub Issues|GitHub Discussions|Pull requests|Projects|Wiki|Security|Insights|Settings|Watch|Star|Fork|Code|Issues|Pull requests|Actions|Projects|Wiki|Security|Insights|Settings|Branches|Tags|Commits|Releases|Packages|Environments|Deployments|Contributors|Graphs|Network|Forks|Stargazers|Watchers|License|Code of conduct|Contributing|Changelog|Readme|Description|Website|Topics|Languages|Stars|Forks|Watchers|Issues|Pull requests|Actions|Projects|Wiki|Security|Insights|Settings).*',
        r'\b(Clone|Download|Open with|Copy|Copy URL|HTTPS|SSH|GitHub CLI|Download ZIP|Launching|If nothing happens|Download|GitHub Desktop|Xcode|Visual Studio Code|Codespaces|Open in|Open with|Copy|Clone|Download).*',
        r'\b(Footer|Header|Navigation|Skip to|Jump to|Main content|Sidebar|Repository|Organization|User|Profile|Notifications|Your repositories|Your stars|Your profile|Settings|Sign out|Switch account|Create repository|Import repository|New repository|New organization|New team|New project|New gist|New codespace).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_juejin(text: str, max_chars: int = 5000) -> str:
    """掘金清理规则"""
    patterns_to_remove = [
        r'\b(掘金|Juejin|版权声明|原创|首发|独家|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|掘金App|掘金客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_jianshu(text: str, max_chars: int = 5000) -> str:
    """简书清理规则"""
    patterns_to_remove = [
        r'\b(简书|Jiangshu|版权声明|原创|首发|独家|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|简书App|简书客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_weixin(text: str, max_chars: int = 5000) -> str:
    """微信公众号文章清理规则"""
    patterns_to_remove = [
        r'\b(微信|公众号|原创|版权声明|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|在看|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_51cto(text: str, max_chars: int = 5000) -> str:
    """51CTO 清理规则"""
    patterns_to_remove = [
        r'\b(51CTO|版权声明|原创|首发|独家|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|51CTOApp|51CTO客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_cnblogs(text: str, max_chars: int = 5000) -> str:
    """博客园清理规则"""
    patterns_to_remove = [
        r'\b(博客园|cnblogs|版权声明|原创|首发|独家|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|博客园App|博客园客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_oschina(text: str, max_chars: int = 5000) -> str:
    """OSChina 清理规则"""
    patterns_to_remove = [
        r'\b(OSChina|开源中国|版权声明|原创|首发|独家|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|OSChinaApp|OSChina客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_segmentfault(text: str, max_chars: int = 5000) -> str:
    """SegmentFault 思否清理规则"""
    patterns_to_remove = [
        r'\b(SegmentFault|思否|版权声明|原创|首发|独家|作者|来源|编辑|责编|审核|发布时间|更新时间|阅读|点赞|评论|分享|收藏|转发|举报|违规|投诉|建议|反馈|客服|联系我们|关于我们|免责声明|隐私政策|用户协议|版权所有|保留所有权利|未经授权|禁止转载|转载请注明|出处|来源|作者简介|个人简介|更多文章|关注作者|订阅专栏|加入圈子|粉丝|关注|私信|消息|通知|设置|退出登录|登录|注册|下载APP|打开APP|扫码|二维码|微信|公众号|小程序|客户端|移动端|PC端|SegmentFaultApp|SegmentFault客户端).*',
        r'\b(广告|推广|赞助|合作|商务合作|招聘|外包|技术支持|客服|QQ群|微信群|交流群).*',
        r'^\s*[\d\.]+\s*$',
        r'^\s*[\-=]{3,}\s*$',
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return clean_generic(cleaned, max_chars)


def clean_juejin_cn(text: str, max_chars: int = 5000) -> str:
    """稀土掘金清理规则"""
    return clean_juejin(text, max_chars)


# ========== 站点清理器注册表 ==========

# 域名 -> 清理函数映射
CLEANER_REGISTRY: Dict[str, Callable[[str, int], str]] = {
    # CSDN
    'blog.csdn.net': clean_csdn,
    'csdn.net': clean_csdn,
    
    # 知乎
    'zhihu.com': clean_zhihu,
    'www.zhihu.com': clean_zhihu,
    'zhuanlan.zhihu.com': clean_zhihu,
    
    # 百家号
    'baijiahao.baidu.com': clean_baijiahao,
    
    # GitHub
    'github.com': clean_github,
    'gist.github.com': clean_github,
    
    # 掘金
    'juejin.cn': clean_juejin,
    'juejin.im': clean_juejin,
    
    # 简书
    'jianshu.com': clean_jianshu,
    'www.jianshu.com': clean_jianshu,
    
    # 微信公众号
    'mp.weixin.qq.com': clean_weixin,
    
    # 51CTO
    'blog.51cto.com': clean_51cto,
    '51cto.com': clean_51cto,
    
    # 博客园
    'cnblogs.com': clean_cnblogs,
    'www.cnblogs.com': clean_cnblogs,
    
    # OSChina
    'oschina.net': clean_oschina,
    'www.oschina.net': clean_oschina,
    'my.oschina.net': clean_oschina,
    
    # SegmentFault
    'segmentfault.com': clean_segmentfault,
    'www.segmentfault.com': clean_segmentfault,
}


# ========== 公共接口 ==========

def get_cleaner_for_url(url: str) -> Callable[[str, int], str]:
    """根据 URL 获取对应的清理函数"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # 精确匹配域名
        if domain in CLEANER_REGISTRY:
            return CLEANER_REGISTRY[domain]
        
        # 尝试匹配父域名（如 blog.csdn.net -> csdn.net）
        parts = domain.split('.')
        for i in range(1, len(parts)):
            parent_domain = '.'.join(parts[i:])
            if parent_domain in CLEANER_REGISTRY:
                return CLEANER_REGISTRY[parent_domain]
        
    except Exception:
        pass
    
    # 默认使用通用清理
    return clean_generic


def clean_detail_content(url: str, text: str, max_chars: int = 5000) -> str:
    """根据 URL 自动选择清理规则并清理内容"""
    cleaner = get_cleaner_for_url(url)
    return cleaner(text, max_chars)


def list_supported_sites() -> List[str]:
    """列出所有支持的站点"""
    return sorted(set(CLEANER_REGISTRY.keys()))


# ========== 测试入口 ==========

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python detail_cleaner.py <url> [text_file]")
        print("支持的站点:")
        for site in list_supported_sites():
            print(f"  - {site}")
        sys.exit(1)
    
    url = sys.argv[1]
    
    if len(sys.argv) > 2:
        with open(sys.argv[2], 'r', encoding='utf-8') as f:
            text = f.read()
    else:
        # 从 stdin 读取
        text = sys.stdin.read()
    
    cleaned = clean_detail_content(url, text)
    print(cleaned)
