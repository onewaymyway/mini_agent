# Browser-CDP 鍔ㄦ€侀〉闈㈡敮鎸佹柟妗?

> 鐢熸垚鏃堕棿锛?026-08-06
> 鐩爣锛氫负 P0 浼樺厛绾ч鍩燂紙鏀垮簻鏈嶅姟銆佸尰鐤楀仴搴枫€佹硶寰嬶級璁捐鍔ㄦ€侀〉闈㈤€傞厤绛栫暐

---

## 涓€銆佺洰鏍囩綉绔欐妧鏈壒寰佸垎鏋?

### 1.1 鏀垮簻鏈嶅姟绫荤綉绔?

| 缃戠珯 | URL | 鎶€鏈壒寰?| 鍔ㄦ€佸唴瀹?| 鍙嶇埇闅惧害 |
|------|-----|---------|---------|---------|
| 涓浗鏀垮簻缃?| gov.cn | 浼犵粺鏈嶅姟绔覆鏌?| 鏂伴椈鍒楄〃銆佹斂绛栨枃浠?| 猸?|
| 涓浗瑁佸垽鏂囦功缃?| wenshu.court.gov.cn | 鍒嗛〉鏌ヨ銆佽〃鍗曟彁浜?| 鍒ゅ喅涔﹀垪琛ㄣ€佽鎯?| 猸愨瓙 |
| 淇＄敤涓浗 | creditchina.gov.cn | 浼佷笟淇＄敤鏌ヨ | 淇＄敤淇℃伅銆佽鏀垮缃?| 猸愨瓙 |
| 鍥藉浼佷笟淇＄敤淇℃伅鍏ず | gsxt.gov.cn | 澶嶆潅琛ㄥ崟銆侀獙璇佺爜 | 浼佷笟宸ュ晢淇℃伅 | 猸愨瓙猸?|

**鎶€鏈寫鎴?*锛?
- 閮ㄥ垎鏀垮簻缃戠珯浣跨敤鑰佹棫鎶€鏈紙jQuery銆佷紶缁熻〃鍗曪級
- 瑁佸垽鏂囦功缃戦渶瑕佸垎椤垫煡璇㈠拰澶嶆潅绛涢€?
- 浼佷笟淇＄敤淇℃伅鍏ず绯荤粺鏈夐獙璇佺爜

### 1.2 鍖荤枟鍋ュ悍绫荤綉绔?

| 缃戠珯 | URL | 鎶€鏈壒寰?| 鍔ㄦ€佸唴瀹?| 鍙嶇埇闅惧害 |
|------|-----|---------|---------|---------|
| 涓侀鍥尰闄㈠簱 | y.dxy.cn | 鍖婚櫌鍒楄〃銆佹帓鍚?| 鍖婚櫌璇︽儏銆佺瀹?| 猸愨瓙 |
| 鎸傚彿缃?| guahao.com | 棰勭害娴佺▼澶嶆潅 | 鍖婚櫌銆佸尰鐢熴€佺瀹?| 猸愨瓙猸?|
| 39灏卞尰鍔╂墜 | 39.net | 鍖婚櫌鎺掕姒?| 鍖婚櫌淇℃伅銆佽瘎浠?| 猸愨瓙 |

**鎶€鏈寫鎴?*锛?
- 鎸傚彿娴佺▼闇€瑕佸姝ラ浜や簰
- 鍖婚櫌鏁版嵁闇€瑕佸垎椤靛拰绛涢€?
- 閮ㄥ垎缃戠珯闇€瑕佺櫥褰曟€?

### 1.3 娉曞緥绫荤綉绔?

| 缃戠珯 | URL | 鎶€鏈壒寰?| 鍔ㄦ€佸唴瀹?| 鍙嶇埇闅惧害 |
|------|-----|---------|---------|---------|
| 涓浗娉曞緥鏈嶅姟缃?| 12348.gov.cn | 娉曞緥娉曡鏌ヨ | 娉曡銆佸緥甯堛€佸挩璇?| 猸?|
| 鍗庡緥缃?| 66law.cn | 娉曞緥鍜ㄨ骞冲彴 | 寰嬪笀淇℃伅銆佹渚?| 猸愨瓙 |
| 鎵炬硶缃?| findlaw.cn | 娉曞緥鏈嶅姟骞冲彴 | 寰嬪笀銆佹硶瑙?| 猸愨瓙 |

**鎶€鏈寫鎴?*锛?
- 娉曞緥娉曡闇€瑕佸垎绫荤瓫閫?
- 寰嬪笀淇℃伅闇€瑕佸垎椤垫煡璇?
- 閮ㄥ垎缃戠珯鏈夌櫥褰曡姹?

---

## 浜屻€佸姩鎬侀〉闈㈡敮鎸佹灦鏋?

### 2.1 鏍稿績缁勪欢

```
src/searchers/
鈹溾攢鈹€ dynamic_page.py      # 鍔ㄦ€侀〉闈㈤€氱敤澶勭悊鍣?
鈹溾攢鈹€ pagination.py        # 鍒嗛〉澶勭悊
鈹溾攢鈹€ form_handler.py      # 琛ㄥ崟鎻愪氦澶勭悊
鈹溾攢鈹€ captcha_handler.py   # 楠岃瘉鐮佸鐞嗭紙宸叉湁锛?
鈹斺攢鈹€ anti_detection.py    # 鍙嶆娴嬪寮?
```

### 2.2 鍔ㄦ€侀〉闈㈠鐞嗗櫒

```python
# dynamic_page.py

class DynamicPageHandler:
    """鍔ㄦ€侀〉闈㈤€氱敤澶勭悊鍣?""
    
    def __init__(self, browser_client):
        self.client = browser_client
        self.wait_timeout = 30
        self.retry_count = 3
    
    async def navigate_with_wait(self, url: str, wait_selector: str = None) -> bool:
        """瀵艰埅骞剁瓑寰呭姩鎬佸唴瀹瑰姞杞?""
        # 1. 瀵艰埅鍒伴〉闈?
        # 2. 绛夊緟鎸囧畾閫夋嫨鍣ㄥ嚭鐜?
        # 3. 绛夊緟椤甸潰绋冲畾锛堟棤鏂板唴瀹瑰姞杞斤級
        # 4. 杩斿洖鏄惁鎴愬姛
        pass
    
    async def scroll_to_load(self, selector: str = None, max_scrolls: int = 5) -> int:
        """婊氬姩椤甸潰鍔犺浇鏇村鍐呭"""
        # 妯℃嫙浜虹被婊氬姩琛屼负
        # 妫€娴嬫柊鍐呭鍔犺浇
        # 杩斿洖鍔犺浇鐨勫唴瀹规暟閲?
        pass
    
    async def wait_for_ajax(self, timeout: int = 10) -> bool:
        """绛夊緟 AJAX 璇锋眰瀹屾垚"""
        # 妫€娴嬮〉闈腑鐨?AJAX 娲诲姩
        # 绛夊緟缃戠粶绌洪棽
        pass
    
    async def handle_pagination(self, 
                                 base_url: str,
                                 page_selector: str,
                                 max_pages: int = 10) -> List[Dict]:
        """澶勭悊鍒嗛〉鏌ヨ"""
        # 閬嶅巻鎵€鏈夐〉闈?
        # 鎻愬彇姣忛〉鏁版嵁
        # 鍚堝苟缁撴灉
        pass
```

### 2.3 鍒嗛〉澶勭悊

```python
# pagination.py

class PaginationHandler:
    """鍒嗛〉澶勭悊鍣?""
    
    def __init__(self, browser_client):
        self.client = browser_client
    
    async def extract_page_info(self) -> Dict:
        """鎻愬彇鍒嗛〉淇℃伅"""
        # 鎬婚〉鏁?
        # 褰撳墠椤?
        # 姣忛〉鏁伴噺
        # 鎬荤粨鏋滄暟
        pass
    
    async def navigate_to_page(self, page: int) -> bool:
        """瀵艰埅鍒版寚瀹氶〉"""
        # 鐐瑰嚮椤电爜
        # 绛夊緟鍐呭鍔犺浇
        # 楠岃瘉瀵艰埅鎴愬姛
        pass
    
    async def auto_scroll_pagination(self,
                                      max_pages: int = 10,
                                      delay_range: Tuple[float, float] = (1.0, 2.0)) -> List[Dict]:
        """鑷姩婊氬姩鍒嗛〉"""
        # 閬嶅巻鎵€鏈夐〉闈?
        # 鎻愬彇鏁版嵁
        # 妯℃嫙浜虹被琛屼负
        pass
```

### 2.4 琛ㄥ崟澶勭悊

```python
# form_handler.py

class FormHandler:
    """琛ㄥ崟鎻愪氦澶勭悊鍣?""
    
    def __init__(self, browser_client):
        self.client = browser_client
    
    async def fill_form(self, 
                        form_selector: str,
                        fields: Dict[str, str]) -> bool:
        """濉啓琛ㄥ崟"""
        # 瀹氫綅琛ㄥ崟鍏冪礌
        # 妯℃嫙浜虹被杈撳叆
        # 澶勭悊鍔ㄦ€佷笅鎷夋
        pass
    
    async def submit_form(self,
                          submit_selector: str = None,
                          wait_selector: str = None) -> bool:
        """鎻愪氦琛ㄥ崟"""
        # 鐐瑰嚮鎻愪氦鎸夐挳
        # 绛夊緟鍝嶅簲
        # 澶勭悊楠岃瘉鐮?
        pass
    
    async def handle_captcha(self, captcha_type: str = "slider") -> bool:
        """澶勭悊楠岃瘉鐮?""
        # 婊戝潡楠岃瘉鐮?
        # 鐐归€夐獙璇佺爜
        # 鏂囧瓧楠岃瘉鐮?
        pass
```

---

## 涓夈€佺洰鏍囩綉绔欓€傞厤绛栫暐

### 3.1 鏀垮簻鏈嶅姟绫婚€傞厤

#### 涓浗鏀垮簻缃?(gov.cn)

```python
# gov_cn_search.py

class GovCnSearcher(BaseSearcher):
    """涓浗鏀垮簻缃戞悳绱㈠櫒"""
    
    BASE_URL = "https://www.gov.cn"
    
    def search(self, query: str, **kwargs) -> List[Dict]:
        # 1. 瀵艰埅鍒版悳绱㈤〉
        # 2. 濉啓鎼滅储妗?
        # 3. 鎻愪氦鎼滅储
        # 4. 绛夊緟缁撴灉鍔犺浇
        # 5. 鎻愬彇鎼滅储缁撴灉
        # 6. 澶勭悊鍒嗛〉
        pass
    
    def _extract_news(self, items: List[Element]) -> List[Dict]:
        # 鎻愬彇鏂伴椈鏍囬銆侀摼鎺ャ€佹棩鏈熴€佹潵婧?
        pass
```

**閫傞厤瑕佺偣**锛?
- 浣跨敤浼犵粺閫夋嫨鍣紙.news-list, .result-item锛?
- 绛夊緟椤甸潰瀹屽叏鍔犺浇
- 澶勭悊鍒嗛〉瀵艰埅

#### 涓浗瑁佸垽鏂囦功缃?(wenshu.court.gov.cn)

```python
# court_search.py

class CourtSearcher(BaseSearcher):
    """涓浗瑁佸垽鏂囦功缃戞悳绱㈠櫒"""
    
    BASE_URL = "https://wenshu.court.gov.cn"
    
    def search(self, query: str, case_type: str = None, **kwargs) -> List[Dict]:
        # 1. 瀵艰埅鍒版悳绱㈤〉
        # 2. 濉啓鎼滅储鏉′欢
        # 3. 鎻愪氦鏌ヨ
        # 4. 绛夊緟缁撴灉鍔犺浇
        # 5. 鎻愬彇鍒ゅ喅涔﹀垪琛?
        # 6. 澶勭悊鍒嗛〉
        # 7. 鍙€夛細鎻愬彇璇︽儏
        pass
    
    def _extract_court_doc(self, item: Element) -> Dict:
        # 鎻愬彇妗堝彿銆佹爣棰樸€佹硶闄€佹棩鏈熴€侀摼鎺?
        pass
```

**閫傞厤瑕佺偣**锛?
- 闇€瑕佸～鍐欏鏉傜瓫閫夋潯浠?
- 澶勭悊鍒嗛〉鏌ヨ
- 绛夊緟缁撴灉鍔犺浇锛堝彲鑳借緝鎱級

#### 鍥藉浼佷笟淇＄敤淇℃伅鍏ず (gsxt.gov.cn)

```python
# gsxt_search.py

class GSXTSearcher(BaseSearcher):
    """鍥藉浼佷笟淇＄敤淇℃伅鍏ず绯荤粺鎼滅储鍣?""
    
    BASE_URL = "https://www.gsxt.gov.cn"
    
    def search(self, company_name: str, **kwargs) -> Dict:
        # 1. 瀵艰埅鍒版煡璇㈤〉
        # 2. 濉啓浼佷笟鍚嶇О
        # 3. 澶勭悊楠岃瘉鐮?
        # 4. 鎻愪氦鏌ヨ
        # 5. 绛夊緟缁撴灉
        # 6. 鎻愬彇浼佷笟淇℃伅
        pass
    
    def _handle_captcha(self) -> bool:
        # 澶勭悊婊戝潡楠岃瘉鐮?
        pass
```

**閫傞厤瑕佺偣**锛?
- 闇€瑕佸鐞嗛獙璇佺爜
- 浼佷笟淇℃伅缁撴瀯澶嶆潅
- 闇€瑕佺櫥褰曟€侊紙鍙€夛級

### 3.2 鍖荤枟鍋ュ悍绫婚€傞厤

#### 涓侀鍥尰闄㈠簱 (y.dxy.cn)

```python
# dxy_hospital_search.py

class DXYHospitalSearcher(BaseSearcher):
    """涓侀鍥尰闄㈠簱鎼滅储鍣?""
    
    BASE_URL = "https://y.dxy.cn"
    
    def search(self, query: str, city: str = None, **kwargs) -> List[Dict]:
        # 1. 瀵艰埅鍒板尰闄㈠簱
        # 2. 濉啓鎼滅储鏉′欢
        # 3. 鎻愪氦鏌ヨ
        # 4. 绛夊緟缁撴灉鍔犺浇
        # 5. 鎻愬彇鍖婚櫌鍒楄〃
        # 6. 澶勭悊鍒嗛〉
        pass
    
    def _extract_hospital(self, item: Element) -> Dict:
        # 鎻愬彇鍖婚櫌鍚嶇О銆佺瓑绾с€佸湴鍖恒€佺瀹ゃ€侀摼鎺?
        pass
```

**閫傞厤瑕佺偣**锛?
- 鍖婚櫌鏁版嵁涓板瘜
- 闇€瑕佸鐞嗙瓫閫夋潯浠?
- 鍒嗛〉鏌ヨ

#### 鎸傚彿缃?(guahao.com)

```python
# guahao_search.py

class GuahaoSearcher(BaseSearcher):
    """鎸傚彿缃戞悳绱㈠櫒"""
    
    BASE_URL = "https://www.guahao.com"
    
    def search(self, hospital: str, department: str = None, **kwargs) -> Dict:
        # 1. 瀵艰埅鍒版寕鍙风綉
        # 2. 鎼滅储鍖婚櫌
        # 3. 閫夋嫨绉戝
        # 4. 鑾峰彇鍖荤敓淇℃伅
        # 5. 鎻愬彇鎸傚彿淇℃伅
        pass
    
    def _extract_doctor(self, item: Element) -> Dict:
        # 鎻愬彇鍖荤敓濮撳悕銆佽亴绉般€佷笓闀裤€佹寕鍙疯垂銆佸彲绾︽椂闂?
        pass
```

**閫傞厤瑕佺偣**锛?
- 娴佺▼澶嶆潅锛堝尰闄⑩啋绉戝鈫掑尰鐢燂級
- 闇€瑕佸姝ラ浜や簰
- 鏁版嵁瀹炴椂鎬ц姹傞珮

---

### 3.3 娉曞緥绫婚€傞厤

#### 鍗庡緥缃?(66law.cn)

```python
# 66law_search.py

class Law66Searcher(BaseSearcher):
    """鍗庡緥缃戞悳绱㈠櫒"""
    
    BASE_URL = "https://www.66law.cn"
    
    def search(self, query: str, law_type: str = None, **kwargs) -> List[Dict]:
        # 1. 瀵艰埅鍒板崕寰嬬綉
        # 2. 濉啓鎼滅储鏉′欢
        # 3. 鎻愪氦鏌ヨ
        # 4. 绛夊緟缁撴灉鍔犺浇
        # 5. 鎻愬彇寰嬪笀淇℃伅
        # 6. 澶勭悊鍒嗛〉
        pass
    
    def _extract_lawyer(self, item: Element) -> Dict:
        # 鎻愬彇寰嬪笀濮撳悕銆佸湴鍖恒€佹搮闀块鍩熴€佹墽涓氬勾闄愩€佽仈绯绘柟寮?
        pass
```

**閫傞厤瑕佺偣**锛?
- 寰嬪笀淇℃伅涓板瘜
- 闇€瑕佸鐞嗙瓫閫夋潯浠?
- 鍒嗛〉鏌ヨ

---

## 鍥涖€佸弽妫€娴嬪寮虹瓥鐣?

### 4.1 浜虹被鍖栬涓烘ā鎷?

```python
# anti_detection.py

class AntiDetection:
    """鍙嶆娴嬪寮?""
    
    def __init__(self, browser_client):
        self.client = browser_client
    
    async def human_like_scroll(self, max_scrolls: int = 5) -> None:
        """妯℃嫙浜虹被婊氬姩琛屼负"""
        for i in range(max_scrolls):
            # 闅忔満婊氬姩璺濈
            scroll_distance = random.randint(200, 800)
            # 闅忔満寤惰繜
            await asyncio.sleep(random.uniform(0.5, 1.5))
            # 鎵ц婊氬姩
            await self.client.scroll_down(scroll_distance)
    
    async def human_like_mouse(self, target_selector: str) -> None:
        """妯℃嫙浜虹被榧犳爣琛屼负"""
        # 绉诲姩鍒扮洰鏍囧厓绱?
        # 闅忔満鎮仠鏃堕棿
        # 妯℃嫙鐐瑰嚮
        pass
    
    async def random_delay(self, min_sec: float = 1.0, max_sec: float = 3.0) -> float:
        """闅忔満寤惰繜"""
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)
        return delay
```

### 4.2 璇锋眰棰戠巼鎺у埗

```python
# rate_limiter.py

class RateLimiter:
    """璇锋眰棰戠巼闄愬埗"""
    
    def __init__(self, max_requests_per_minute: int = 10):
        self.max_requests = max_requests_per_minute
        self.timestamps = []
    
    async def wait_if_needed(self) -> None:
        """妫€鏌ュ苟绛夊緟"""
        now = time.time()
        # 绉婚櫎瓒呰繃1鍒嗛挓鐨勮褰?
        self.timestamps = [t for t in self.timestamps if now - t < 60]
        
        if len(self.timestamps) >= self.max_requests:
            # 绛夊緟鐩村埌鍙互鍙戦€佹柊璇锋眰
            wait_time = 60 - (now - self.timestamps[0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        
        self.timestamps.append(time.time())
```

### 4.3 浠ｇ悊姹犻泦鎴?

```python
# proxy_pool.py

class ProxyPool:
    """浠ｇ悊姹犵鐞?""
    
    def __init__(self, proxy_file: str = "proxies.json"):
        self.proxies = self._load_proxies(proxy_file)
        self.current_index = 0
    
    def get_next_proxy(self) -> Dict:
        """鑾峰彇涓嬩竴涓唬鐞?""
        proxy = self.proxies[self.current_index % len(self.proxies)]
        self.current_index += 1
        return proxy
    
    def mark_failed(self, proxy: Dict) -> None:
        """鏍囪浠ｇ悊澶辫触"""
        proxy["failed_count"] = proxy.get("failed_count", 0) + 1
        if proxy["failed_count"] > 3:
            self.proxies.remove(proxy)
```
