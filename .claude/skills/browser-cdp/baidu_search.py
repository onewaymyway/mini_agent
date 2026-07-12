#!/usr/bin/env python
"""
百度搜索自动化脚本

使用 browser-cdp skill 进行百度搜索并获取详细内容。

用法：
    python baidu_search.py "搜索关键词" [--max-results N] [--output-dir DIR] [--headless] [--port PORT]

示例：
    python baidu_search.py "自主进化Agent" --max-results 5
    python baidu_search.py "Python教程" --max-results 3 --output-dir ./results
    python baidu_search.py "AI Agent" --port 9333 --headless
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional


SKILL_DIR = Path(__file__).parent
PYTHON_CMD = sys.executable  # 使用当前Python解释器

# ========== 反爬策略配置 ==========
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

MIN_DELAY = 1.5  # 最小请求间隔（秒）
MAX_DELAY = 3.5  # 最大请求间隔（秒）
MAX_RETRIES = 3  # 最大重试次数
BASE_RETRY_DELAY = 2.0  # 基础重试延迟（秒）
MAX_RETRY_DELAY = 30.0  # 最大重试延迟（秒）


def random_delay(min_sec: float = MIN_DELAY, max_sec: float = MAX_DELAY) -> float:
    """随机延迟，返回实际延迟时间"""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
    return delay


def get_random_ua() -> str:
    """获取随机 User-Agent"""
    return random.choice(USER_AGENTS)


def exponential_backoff(attempt: int, base_delay: float = BASE_RETRY_DELAY, max_delay: float = MAX_RETRY_DELAY) -> float:
    """指数退避延迟，返回实际延迟时间"""
    delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
    time.sleep(delay)
    return delay


def run_cmd_with_retry(cmd: List[str], cwd: Path = None, capture: bool = True, max_retries: int = MAX_RETRIES) -> subprocess.CompletedProcess:
    """带重试机制的命令执行"""
    if cwd is None:
        cwd = SKILL_DIR
    
    last_error = None
    for attempt in range(max_retries + 1):
        print(f"  [CMD] {' '.join(cmd)} (尝试 {attempt + 1}/{max_retries + 1})")
        result = subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True, timeout=60)
        
        if result.returncode == 0:
            return result
        
        last_error = result.stderr
        print(f"  [WARN] 返回码: {result.returncode}, 错误: {last_error[:200] if last_error else '无'}")
        
        if attempt < max_retries:
            delay = exponential_backoff(attempt)
            print(f"  [RETRY] {delay:.1f}秒后重试...")
    
    # 所有重试都失败，返回最后一次结果
    return result


def run_cmd(cmd: List[str], cwd: Path = None, capture: bool = True) -> subprocess.CompletedProcess:
    """运行命令并返回结果（兼容旧接口，内部使用重试机制）"""
    return run_cmd_with_retry(cmd, cwd, capture)


def ensure_browser(port: int = 9333, name: str = "baidu_search", headless: bool = False, start_url: str = "https://www.baidu.com", user_agent: str = None) -> Dict:
    """确保浏览器实例运行，返回 {port, tab_id}"""
    print(f"[浏览器] 启动/连接专用浏览器实例: {name} (端口: {port})")
    
    if user_agent is None:
        user_agent = get_random_ua()
    
    cmd = [
        PYTHON_CMD, "browser_launch.py",
        "--dedicated",
        "--name", name,
        "--port", str(port),
        "--start-url", start_url,
    ]
    if user_agent:
        cmd.extend(["--user-agent", user_agent])
    if headless:
        cmd.append("--headless")
    
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"浏览器启动失败: {result.stderr}")
    
    # 解析输出获取 tab_id
    tab_id = None
    for line in result.stdout.split('\n'):
        if '首个 tab id=' in line:
            # 格式: "首个 tab id=F98DABBEEE509237BF42A43A14FB7F39"
            tab_id = line.split('首个 tab id=')[1].split('\r')[0].strip()
            break
        elif 'tab id=' in line and '首个' not in line:
            # 格式: "tab id=F98DABBEEE509237BF42A43A14FB7F39"
            tab_id = line.split('tab id=')[1].split('\r')[0].strip()
            break
    
    if not tab_id:
        # 尝试列出tabs获取第一个（解析JSON输出）
        list_result = run_cmd([PYTHON_CMD, "browser_launch.py", "--list", "--port", str(port)])
        try:
            tabs = json.loads(list_result.stdout.strip())
            if tabs and isinstance(tabs, list) and len(tabs) > 0:
                tab_id = tabs[0].get('id')
        except json.JSONDecodeError:
            # 备选：按行解析
            for line in list_result.stdout.split('\n'):
                line = line.strip()
                if line and not line.startswith('[') and not line.startswith('->'):
                    parts = line.split()
                    if len(parts) >= 1 and len(parts[0]) > 10:
                        tab_id = parts[0]
                        break
    
    # 如果仍然没有 tab_id，尝试重新获取
    if not tab_id:
        print(f"[WARN] 未能解析 tab_id，尝试重新获取...")
        time.sleep(2)
        list_result = run_cmd([PYTHON_CMD, "browser_launch.py", "--list", "--port", str(port)])
        try:
            tabs = json.loads(list_result.stdout.strip())
            if tabs and isinstance(tabs, list) and len(tabs) > 0:
                tab_id = tabs[0].get('id')
        except json.JSONDecodeError:
            pass
    
    if not tab_id:
        raise RuntimeError(f"无法获取 tab_id，浏览器可能未完全就绪")
    
    print(f"[浏览器] 就绪 - 端口: {port}, Tab ID: {tab_id}")
    return {"port": port, "tab_id": tab_id}


def search_baidu(port: int, tab_id: str, query: str, max_results: int = 10, wait_timeout: int = 15) -> List[Dict]:
    """在百度搜索并返回结果链接列表"""
    print(f"[搜索] 正在搜索: {query}")
    
    # 请求前随机延迟
    delay = random_delay()
    print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
    
    # 直接访问搜索结果URL（比点击按钮更可靠）
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.baidu.com/s?wd={encoded_query}"
    
    # 1. 直接访问搜索结果页
    run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id, "--goto", search_url, "--wait-selector", "#content_left", "--timeout", str(wait_timeout)])
    time.sleep(1)
    
    # 搜索后随机延迟
    delay = random_delay(0.5, 1.5)
    print(f"  [延迟] 搜索后等待 {delay:.1f} 秒")
    
    # 2. 使用 JavaScript 直接提取搜索结果（更准确）
    js_code = r"""
(() => {
  const results = [];
  // 百度搜索结果容器选择器
  const containers = document.querySelectorAll('#content_left .result, #content_left .c-container[srcid], .result.c-container');
  containers.forEach((container, index) => {
    // 获取标题
    const titleEl = container.querySelector('h3 a, h3, .t a, .c-title a, a[mu]');
    const title = titleEl ? (titleEl.innerText || titleEl.textContent || '').trim() : '';
    
    // 获取链接
    const linkEl = container.querySelector('h3 a, .t a, .c-title a, a[mu]');
    const url = linkEl ? linkEl.href : '';
    
    // 获取摘要
    const abstractEl = container.querySelector('.c-abstract, .c-span-last, .c-span9, [class*="abstract"]');
    const snippet = abstractEl ? (abstractEl.innerText || abstractEl.textContent || '').trim() : '';
    
    if (title && url && url.startsWith('http')) {
      results.push({title, url, snippet});
    }
  });
  return results;
})()
"""
    
    result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port), "--tab", tab_id, "--eval", js_code])

    if result.returncode != 0:
        print(f"[警告] JS提取失败，尝试备选方案: {result.stderr[:200]}")
        # 备选：使用 browser_extract
        result = run_cmd([PYTHON_CMD, "browser_extract.py", "--port", str(port), "--tab", tab_id, "--mode", "links", "--max-chars", "10000"])
        if result.returncode != 0:
            return []
        try:
            links = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return []
        
        results = []
        for link in links:
            href = link.get('href', '')
            text = link.get('text', '').strip()
            if href and text and len(text) > 2 and href.startswith('http://www.baidu.com/link?'):
                results.append({'title': text, 'url': href, 'snippet': ''})
                if len(results) >= max_results:
                    break
        return results
    
    try:
        # browser_console.py 输出格式: {"result": [...]}
        output = json.loads(result.stdout.strip())
        results = output.get('result', [])
    except json.JSONDecodeError:
        print(f"[警告] 无法解析JS结果: {result.stdout[:200]}")
        return []    
    # 过滤和限制数量
    filtered = []
    for r in results:
        if r.get('title') and r.get('url'):
            filtered.append({
                'title': r['title'],
                'url': r['url'],
                'snippet': r.get('snippet', '')
            })
            if len(filtered) >= max_results:
                break
    
    print(f"[搜索] 找到 {len(filtered)} 个有效结果")
    return filtered


def fetch_detail(port: int, tab_id: str, url: str, wait_timeout: int = 15, max_chars: int = 5000, max_retries: int = 3) -> Dict:
    """访问详情页并提取文本内容（带重试）"""
    
    for attempt in range(max_retries):
        try:
            print(f"  [详情] 正在访问: {url[:80]}... (尝试 {attempt + 1}/{max_retries})")
            
            # 请求前随机延迟
            delay = random_delay(1.0, 2.5)
            print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
            
            # 导航到详情页
            nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id, "--goto", url, "--wait-selector", "body", "--timeout", str(wait_timeout)])
            if nav_result.returncode != 0:
                if attempt < max_retries - 1:
                    wait_time = exponential_backoff(attempt)
                    print(f"  [重试] 导航失败，{wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                return {'url': url, 'content': '', 'success': False, 'error': '导航失败'}
            
            time.sleep(1.5)  # 等待页面渲染
            
            # 提取文本内容
            extract_result = run_cmd([PYTHON_CMD, "browser_extract.py", "--port", str(port), "--tab", tab_id, "--mode", "text", "--max-chars", str(max_chars)])
            
            if extract_result.returncode != 0:
                if attempt < max_retries - 1:
                    wait_time = exponential_backoff(attempt)
                    print(f"  [重试] 提取失败，{wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                return {'url': url, 'content': '', 'success': False, 'error': '提取失败'}
            
            text = extract_result.stdout.strip()
            
            # 简单清理：移除CSS/JS代码行
            lines = text.split('\n')
            cleaned_lines = []
            for line in lines:
                line = line.strip()
                if not line or len(line) < 10:
                    continue
                # 跳过明显的CSS/JS代码行
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
            
            cleaned_text = '\n'.join(cleaned_lines[:200])  # 限制行数
            
            return {
                'url': url,
                'content': cleaned_text[:max_chars],
                'success': True
            }
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = exponential_backoff(attempt)
                print(f"  [重试] 异常: {e}，{wait_time:.1f} 秒后重试...")
                time.sleep(wait_time)
                continue
            return {'url': url, 'content': '', 'success': False, 'error': str(e)}
    
    return {'url': url, 'content': '', 'success': False, 'error': '重试次数耗尽'}


def take_screenshot(port: int, tab_id: str, output_path: str, annotate: bool = False) -> bool:
    """截图"""
    cmd = [PYTHON_CMD, "browser_screenshot.py", "--port", str(port), "--tab", tab_id, "--out", output_path]
    if annotate:
        cmd.append("--annotate")
    result = run_cmd(cmd)
    return result.returncode == 0


def save_results(results: List[Dict], output_dir: Path, query: str):
    """保存搜索结果到文件"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存JSON
    json_file = output_dir / f"baidu_search_{query.replace(' ', '_')}.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[保存] JSON结果已保存到: {json_file}")
    
    # 保存Markdown摘要
    md_file = output_dir / f"baidu_search_{query.replace(' ', '_')}.md"
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(f"# 百度搜索结果: {query}\n\n")
        f.write(f"搜索时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        for i, r in enumerate(results, 1):
            f.write(f"## {i}. {r['title']}\n\n")
            f.write(f"**链接**: {r['url']}\n\n")
            if r.get('content'):
                f.write(f"**内容摘要**:\n{r['content'][:1000]}\n\n")
            f.write("---\n\n")
    print(f"[保存] Markdown摘要已保存到: {md_file}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数量 (默认: 10)")
    parser.add_argument("--output-dir", default="./search_results", help="输出目录 (默认: ./search_results)")
    parser.add_argument("--port", type=int, default=9333, help="CDP调试端口 (默认: 9333)")
    parser.add_argument("--name", default="baidu_search", help="浏览器实例名称 (默认: baidu_search)")
    parser.add_argument("--headless", action="store_true", help="无头模式运行")
    parser.add_argument("--wait-timeout", type=int, default=15, help="等待超时秒数 (默认: 15)")
    parser.add_argument("--max-chars", type=int, default=5000, help="详情页最大字符数 (默认: 5000)")
    parser.add_argument("--no-detail", action="store_true", help="不获取详情页内容，只获取搜索结果列表")
    parser.add_argument("--screenshot", action="store_true", help="搜索结果页截图")
    
    args = parser.parse_args()
    
    print(f"{'='*60}")
    print(f"百度搜索自动化")
    print(f"关键词: {args.query}")
    print(f"最大结果: {args.max_results}")
    print(f"输出目录: {args.output_dir}")
    print(f"{'='*60}")
    
    try:
        # 1. 确保浏览器运行
        user_agent = get_random_ua()
        browser_info = ensure_browser(
            port=args.port,
            name=args.name,
            headless=args.headless,
            user_agent=user_agent
        )
        port = browser_info["port"]
        tab_id = browser_info["tab_id"]
        
        # 2. 执行搜索
        results = search_baidu(
            port=port,
            tab_id=tab_id,
            query=args.query,
            max_results=args.max_results,
            wait_timeout=args.wait_timeout
        )
        
        if not results:
            print("[警告] 未找到有效搜索结果")
            return 1
        
        # 3. 可选：截图搜索结果页
        if args.screenshot:
            screenshot_path = Path(args.output_dir) / f"search_{args.query.replace(' ', '_')}.png"
            take_screenshot(port, tab_id, str(screenshot_path), annotate=True)
            print(f"[截图] 已保存到: {screenshot_path}")
        
        # 4. 获取详情页内容
        if not args.no_detail:
            print(f"[详情] 正在获取 {len(results)} 个结果的详细内容...")
            for i, result in enumerate(results):
                print(f"  [{i+1}/{len(results)}] {result['title'][:50]}...")
                detail = fetch_detail(
                    port=port,
                    tab_id=tab_id,
                    url=result['url'],
                    wait_timeout=args.wait_timeout,
                    max_chars=args.max_chars
                )
                result['content'] = detail.get('content', '')
                result['detail_success'] = detail.get('success', False)
                if not detail.get('success'):
                    result['detail_error'] = detail.get('error', '未知错误')
                time.sleep(0.5)  # 避免请求过快
        
        # 5. 保存结果
        save_results(results, Path(args.output_dir), args.query)
        
        # 6. 打印摘要
        print(f"\n{'='*60}")
        print(f"搜索完成！共 {len(results)} 个结果")
        print(f"{'='*60}")
        for i, r in enumerate(results, 1):
            status = "✓" if r.get('detail_success') else "✗"
            print(f"  {i}. {status} {r['title'][:60]}")
            print(f"     {r['url']}")
            if r.get('content'):
                print(f"     摘要: {r['content'][:100]}...")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n[中断] 用户取消操作")
        return 130
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
EOF
