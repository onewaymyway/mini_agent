#!/usr/bin/env python
"""
train_search.py - 12306 铁路购票搜索器

使用 browser-cdp skill 搜索 12306，获取车次、余票、票价信息。

用法:
    python train_search.py "北京" "上海" --date 2026-08-10
    python train_search.py "广州" "深圳" --type G
    python train_search.py "北京" "上海" --output-dir ./train_results

示例:
    python train_search.py "北京" "上海" --date 2026-08-10
    python train_search.py "广州" "深圳" --type G --output-dir ./results
"""

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import random_delay, save_results


# ========== 12306 专用配置 ==========
TRAIN_BASE = "https://www.12306.cn"
TRAIN_SEARCH_URL = f"{TRAIN_BASE}/index/"
TRAIN_TICKET_URL = "https://www.12306.cn/otn/leftTicket/query"

# 默认输出目录
TRAIN_OUTPUT_DIR = Path(__file__).parent.parent.parent / "search_results" / "train"


class TrainSearcher(BaseSearcher):
    """12306 铁路购票搜索器"""

    @property
    def source_name(self) -> str:
        return "train_12306"

    @property
    def supported_types(self) -> List[str]:
        return ["train_search", "ticket_query", "train_schedule"]

    def search(
        self,
        from_station: str,
        to_station: str,
        date: Optional[str] = None,
        train_type: Optional[str] = None,
        max_results: int = 30,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索车次信息

        Args:
            from_station: 出发站
            to_station: 到达站
            date: 出发日期 (YYYY-MM-DD)
            train_type: 车次类型 (G/D/C/Z/T/K/空)
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            车次列表
        """
        if date is None:
            date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        print(f"[12306] 正在查询: {from_station} -> {to_station} ({date})")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(2.0, 3.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 步骤1: 导航到 12306 查询页
        print(f"  [URL] 查询页面: {TRAIN_BASE}/index/")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", TRAIN_BASE + "/index/",
            "--wait-selector", ".search-box, .query-box, #queryLeftTable",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 步骤2: 填写查询表单
        js_fill_form = f"""
(() => {{
  // 填写出发站
  const fromInput = document.querySelector('#fromStationText input, .form-input[from="fromStation"]');
  if (fromInput) {{
    fromInput.value = '{from_station}';
    fromInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
  }}
  
  // 填写到达站
  const toInput = document.querySelector('#toStationText input, .form-input[to="toStation"]');
  if (toInput) {{
    toInput.value = '{to_station}';
    toInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
  }}
  
  // 填写日期
  const dateInput = document.querySelector('#train_date input, .form-input[type="date"]');
  if (dateInput) {{
    dateInput.value = '{date}';
  }}
  
  return {{ from: '{from_station}', to: '{to_station}', date: '{date}' }};
}})()
"""
        fill_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_fill_form,
        ])

        time.sleep(1.5)

        # 步骤3: 点击查询按钮
        js_click_search = r"""
(() => {
  const btn = document.querySelector('#query_ticket, .search-btn, input[value="查询"]');
  if (btn) {
    btn.click();
    return { clicked: true };
  }
  return { clicked: false };
})()
"""
        click_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_click_search,
        ])

        # 等待结果加载
        time.sleep(3.0)

        # 步骤4: 提取车次信息
        js_extract = r"""
(() => {
  const results = [];
  const rows = document.querySelectorAll('#queryLeftTable tr, .table-list tr, tr[data-trainno]');
  rows.forEach((row, i) => {
    if (i === 0) return; // 跳过表头
    const cells = row.querySelectorAll('td');
    if (cells.length < 8) return;
    
    const trainNo = cells[1] ? cells[1].innerText.trim() : '';
    const from = cells[2] ? cells[2].innerText.trim() : '';
    const to = cells[3] ? cells[3].innerText.trim() : '';
    const departTime = cells[4] ? cells[4].innerText.trim() : '';
    const arriveTime = cells[5] ? cells[5].innerText.trim() : '';
    const duration = cells[6] ? cells[6].innerText.trim() : '';
    
    // 二等座
    const secondClass = cells[7] ? cells[7].innerText.trim() : '';
    // 一等座
    const firstClass = cells[8] ? cells[8].innerText.trim() : '';
    // 商务座
    const business = cells[9] ? cells[9].innerText.trim() : '';
    
    if (trainNo) {
      results.push({
        train_no: trainNo,
        from_station: from,
        to_station: to,
        depart_time: departTime,
        arrive_time: arriveTime,
        duration: duration,
        second_class: secondClass,
        first_class: firstClass,
        business: business,
      });
    }
  });
  return results;
})()
"""
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_extract,
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 车次信息提取失败: {extract_result.stderr[:200]}")
            return []

        try:
            trains = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []

        # 过滤车次类型
        if train_type:
            trains = [t for t in trains if t.get('train_no', '').startswith(train_type)]

        if not trains:
            print(f"[提示] 未找到符合条件的车次，尝试备用方式...")
            return self._search_fallback(from_station, to_station, date, port, tab_id, max_results, stealth, output_dir, wait_timeout)

        print(f"  [结果] 找到 {len(trains)} 条车次信息")

        # 保存结果
        final_results = trains[:max_results]
        if output_dir:
            path = save_results(
                final_results,
                output_dir,
                f"train_{from_station}_{to_station}_{date}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return final_results

    def _search_fallback(
        self,
        from_station: str,
        to_station: str,
        date: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        output_dir: Optional[str],
        wait_timeout: int,
    ) -> List[Dict]:
        """备用搜索方法"""
        print(f"  [备用] 尝试使用 API 方式查询...")
        
        # 尝试直接访问 API
        api_url = f"{TRAIN_BASE}/otn/leftTicket/query?leftTicketDTO.train_date={date}&leftTicketDTO.from_station={quote(from_station)}&leftTicketDTO.to_station={quote(to_station)}&purpose_codes=ADULT"
        
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", api_url,
            "--wait-selector", "body",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] API 查询失败")
            return []

        time.sleep(2.0)
        
        # 提取 API 返回的 JSON
        js_api = r"""
(() => {
  const data = window.__INITIAL_STATE__ || {};
  const queryLeftTicket = data.queryLeftTicket || {};
  const tickets = queryLeftTicket.queryLeftTicketDTOs || [];
  
  const results = [];
  tickets.forEach(t => {
    results.push({
      train_no: t.station_train_code,
      from_station: t.from_station_name,
      to_station: t.to_station_name,
      depart_time: t.start_time,
      arrive_time: t.arrive_time,
      duration: t.lx_date,
      second_class: t.queryLeftTicketDTO?.seat_types?.['2'] || '-',
      first_class: t.queryLeftTicketDTO?.seat_types?.['1'] || '-',
      business: t.queryLeftTicketDTO?.seat_types?.['P'] || '-',
    });
  });
  return results;
})()
"""
        api_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_api,
        ])

        try:
            trains = json.loads(api_result.stdout)
        except:
            return []

        return trains[:max_results]

    def get_station_code(self, station_name: str, port: int = 9333, tab_id: Optional[str] = None) -> str:
        """获取车站拼音码"""
        # 常见车站拼音码映射
        station_map = {
            "北京": "BJP",
            "上海": "SHH",
            "广州": "IZQ",
            "深圳": "SZQ",
            "杭州": "HZH",
            "南京": "NJP",
            "成都": "CDW",
            "武汉": "WHN",
            "西安": "XAY",
            "重庆": "CQW",
            "天津": "TJP",
            "长沙": "CSQ",
            "郑州": "ZZF",
            "沈阳": "SYT",
            "哈尔滨": "HRB",
            "济南": "JNK",
            "福州": "FZS",
            "南昌": "NCH",
            "昆明": "KMM",
            "贵阳": "KMG",
            "大连": "DLT",
            "青岛": "QDK",
            "厦门": "XMS",
            "宁波": "NGB",
            "苏州": "SOH",
            "无锡": "WUX",
            "合肥": "HFH",
            "石家庄": "SJP",
            "太原": "TYV",
            "兰州": "LZW",
            "乌鲁木齐": "WMQ",
            "拉萨": "LSO",
            "呼和浩特": "HHT",
            "南宁": "NNZ",
            "海口": "HKU",
            "长春": "CCT",
        }
        return station_map.get(station_name, station_name[:3].upper())


def ensure_browser(port: int = 9333, stealth: bool = True) -> Dict:
    """确保浏览器已连接"""
    cmd = [
        PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
        "--port", str(port),
        "--status",
    ]
    result = run_cmd(cmd)
    
    if result.returncode == 0:
        try:
            status = json.loads(result.stdout)
            if status.get("connected"):
                return {"tab_id": status.get("tab_id"), "port": port}
        except:
            pass
    
    # 启动新浏览器
    cmd = [
        PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
        "--port", str(port),
        "--launch",
    ]
    if stealth:
        cmd.extend(["--stealth"])
    
    result = run_cmd(cmd)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return data
        except:
            pass
    
    return {"error": "浏览器启动失败"}


def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    """执行命令"""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="12306 铁路购票搜索器 - 查询车次和余票信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python train_search.py "北京" "上海" --date 2026-08-10
    python train_search.py "广州" "深圳" --type G
    python train_search.py "北京" "上海" --output-dir ./train_results
"""
    )

    parser.add_argument("from_station", help="出发站（如：北京、上海）")
    parser.add_argument("to_station", help="到达站（如：上海、广州）")
    parser.add_argument("--date", type=str, default=None, help="出发日期 (YYYY-MM-DD, 默认: 明天)")
    parser.add_argument("--type", type=str, default=None, help="车次类型 (G/D/C/Z/T/K)")
    parser.add_argument("--max-results", type=int, default=30, help="最大结果数 (默认: 30)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = TrainSearcher()

    # 执行搜索
    results = searcher.search(
        from_station=args.from_station,
        to_station=args.to_station,
        date=args.date,
        train_type=args.type,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共获取 {len(results)} 条车次信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到车次信息")


if __name__ == "__main__":
    main()
