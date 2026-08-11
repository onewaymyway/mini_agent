"""
stealth.py - 反检测模块

隐藏自动化特征，模拟真实浏览器行为。

核心功能：
- 移除 navigator.webdriver 属性
- 模拟真实浏览器指纹
- 人类行为模拟（鼠标轨迹、打字节奏）
- 请求间隔随机化
- 设备指纹模拟（Canvas/WebGL/内存/硬件并发）
"""
from __future__ import annotations

import asyncio
import random
import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FingerprintConfig:
    """设备指纹配置"""
    canvas_noise_level: float = 0.1
    webgl_vendors: list = None
    memory_range: tuple = (4, 16)
    cores_range: tuple = (2, 16)

    def __post_init__(self):
        if self.webgl_vendors is None:
            self.webgl_vendors = [
                ('Intel Inc.', 'Intel Iris OpenGL Engine'),
                ('NVIDIA Corporation', 'NVIDIA GeForce GTX 1650'),
                ('Apple Inc.', 'Apple GPU'),
                ('AMD', 'AMD Radeon Pro'),
            ]


@dataclass
class StealthConfig:
    """Stealth 配置"""
    enable_webdriver_removal: bool = True
    enable_chrome_runtime: bool = True
    enable_permissions_mock: bool = True
    enable_language_mock: bool = True
    enable_platform_mock: bool = True
    enable_plugins_mock: bool = True
    enable_fingerprint_mock: bool = True  # 新增：设备指纹模拟
    humanize_mouse: bool = True
    humanize_typing: bool = True
    random_delay_range: tuple = (0.5, 2.0)  # 随机延迟范围（增加到人类化水平）
    fingerprint_config: FingerprintConfig = None
    website_type: str = 'general'  # 网站类型：general/ecommerce/finance/social

    def __post_init__(self):
        if self.fingerprint_config is None:
            self.fingerprint_config = FingerprintConfig()


class RequestIntervalController:
    """请求间隔控制器，模拟人类浏览节奏"""
    
    def __init__(self, min_interval: float = 1.0, max_interval: float = 3.0):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.last_request_time = 0
    
    async def wait_if_needed(self):
        """等待必要的时间间隔"""
        now = time.time()
        elapsed = now - self.last_request_time
        
        if elapsed < self.min_interval:
            delay = random.uniform(
                self.min_interval - elapsed,
                self.max_interval - elapsed
            )
            await asyncio.sleep(delay)
        
        self.last_request_time = time.time()


class DynamicFingerprintGenerator:
    """动态指纹生成器 - 为每个会话生成唯一指纹"""
    
    def __init__(self, config: FingerprintConfig = None):
        self.config = config or FingerprintConfig()
        self._session_fingerprint = None
    
    def generate_session_fingerprint(self) -> dict:
        """为每个会话生成唯一指纹"""
        vendor, renderer = random.choice(self.config.webgl_vendors)
        self._session_fingerprint = {
            'canvas_hash': self._generate_canvas_hash(),
            'webgl_renderer': renderer,
            'webgl_vendor': vendor,
            'device_memory': random.randint(*self.config.memory_range),
            'hardware_concurrency': random.randint(*self.config.cores_range),
        }
        return self._session_fingerprint
    
    def _generate_canvas_hash(self) -> str:
        """生成 Canvas 噪声哈希"""
        import hashlib
        noise = bytes([random.randint(0, 255) for _ in range(64)])
        return hashlib.md5(noise).hexdigest()[:16]
    
    def get_session_js(self) -> str:
        """生成会话专属的 JS 指纹脚本"""
        if self._session_fingerprint is None:
            self.generate_session_fingerprint()

        fp = self._session_fingerprint
        vendor = fp['webgl_vendor']
        renderer = fp['webgl_renderer']
        memory = fp['device_memory']
        cores = fp['hardware_concurrency']

        js = f"""
        // Canvas 指纹随机化
        const originalCanvasToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {{
            if (type === 'image/png') {{
                return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
            }}
            return originalCanvasToDataURL.call(this, type, ...args);
        }};

        // WebGL 指纹随机化
        const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {{
            if (param === 37445) return '{vendor}';
            if (param === 37446) return '{renderer}';
            return originalGetParameter.call(this, param);
        }};

        // 设备信息随机化
        Object.defineProperty(navigator, 'deviceMemory', {{
            get: () => {memory}
        }});
        Object.defineProperty(navigator, 'hardwareConcurrency', {{
            get: () => {cores}
        }});
        """
        return js


class AdaptiveBehaviorSimulator:
    """自适应行为模拟器 - 根据网站类型调整行为参数"""
    
    BEHAVIOR_PARAMS = {
        'general': {
            'mouse_speed_range': (200, 800),
            'click_delay_range': (0.1, 0.5),
            'typing_speed_range': (50, 150),
            'scroll_speed_range': (100, 500),
        },
        'ecommerce': {
            'mouse_speed_range': (150, 600),
            'click_delay_range': (0.2, 0.8),
            'typing_speed_range': (30, 100),
            'scroll_speed_range': (50, 300),
        },
        'finance': {
            'mouse_speed_range': (300, 1000),
            'click_delay_range': (0.05, 0.3),
            'typing_speed_range': (100, 200),
            'scroll_speed_range': (200, 800),
        },
        'social': {
            'mouse_speed_range': (180, 700),
            'click_delay_range': (0.15, 0.6),
            'typing_speed_range': (40, 120),
            'scroll_speed_range': (80, 400),
        },
    }
    
    def __init__(self, website_type: str = 'general'):
        self.website_type = website_type
        self.params = self.BEHAVIOR_PARAMS.get(website_type, self.BEHAVIOR_PARAMS['general'])
    
    def get_click_delay(self) -> float:
        return random.uniform(*self.params['click_delay_range'])
    
    def get_typing_delay(self) -> float:
        return random.uniform(1.0 / self.params['typing_speed_range'][1],
                             1.0 / self.params['typing_speed_range'][0])


class StealthMode:
    """
    Stealth 模式：隐藏自动化特征
    
    模拟真实浏览器行为，降低被检测风险
    """
    
    def __init__(self, session, config: StealthConfig = None):
        self.session = session
        self.config = config or StealthConfig()
        self._applied = False
        self.interval_controller = RequestIntervalController()
        self.fingerprint_gen = DynamicFingerprintGenerator(self.config.fingerprint_config)
        self.behavior_sim = AdaptiveBehaviorSimulator(self.config.website_type)
    
    async def apply(self) -> bool:
        """
        应用所有 stealth 脚本
        
        Returns:
            bool: 是否成功应用
        """
        if self._applied:
            logger.debug("Stealth 模式已应用，跳过")
            return True
        
        try:
            # 1. 移除 navigator.webdriver
            if self.config.enable_webdriver_removal:
                await self._remove_webdriver()
            
            # 2. 模拟 Chrome runtime
            if self.config.enable_chrome_runtime:
                await self._mock_chrome_runtime()
            
            # 3. 模拟 permissions.query
            if self.config.enable_permissions_mock:
                await self._mock_permissions()
            
            # 4. 模拟真实语言
            if self.config.enable_language_mock:
                await self._mock_language()
            
            # 5. 模拟真实平台
            if self.config.enable_platform_mock:
                await self._mock_platform()
            
            # 6. 模拟插件
            if self.config.enable_plugins_mock:
                await self._mock_plugins()
            
            # 7. 模拟设备指纹
            if self.config.enable_fingerprint_mock:
                await self._mock_device_fingerprint()
            
            self._applied = True
            logger.info("Stealth 模式应用成功")
            return True
            
        except Exception as e:
            logger.error(f"Stealth 模式应用失败: {e}")
            return False
    
    async def _remove_webdriver(self):
        """移除 navigator.webdriver 属性"""
        js = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
        """
        await self.session.eval_js(js)
        logger.debug("已移除 navigator.webdriver")
    
    async def _mock_chrome_runtime(self):
        """模拟 Chrome runtime 对象"""
        js = """
        window.chrome = {
            runtime: {
                connect: () => ({
                    onMessage: { setListener: () => {} },
                    onDisconnect: { setListener: () => {} }
                }),
                sendMessage: () => Promise.resolve()
            },
            loadTimes: () => ({}),
            csi: () => ({}),
            app: {}
        };
        """
        await self.session.eval_js(js)
        logger.debug("已模拟 Chrome runtime")
    
    async def _mock_permissions(self):
        """模拟 permissions.query"""
        js = """
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        """
        await self.session.eval_js(js)
        logger.debug("已模拟 permissions.query")
    
    async def _mock_language(self):
        """模拟真实语言设置"""
        js = """
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en-US', 'en']
        });
        Object.defineProperty(navigator, 'language', {
            get: () => 'zh-CN'
        });
        """
        await self.session.eval_js(js)
        logger.debug("已模拟语言设置")
    
    async def _mock_platform(self):
        """模拟真实平台信息"""
        js = """
        try {
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32',
                configurable: true
            });
        } catch(e) {}
        try {
            Object.defineProperty(navigator, 'oscpu', {
                get: () => 'Windows NT 10.0; Win64; x64',
                configurable: true
            });
        } catch(e) {}
        """
        await self.session.eval_js(js)
        logger.debug("已模拟平台信息")
    
    async def _mock_plugins(self):
        """模拟浏览器插件"""
        js = """
        // 模拟 PDF 插件
        const mockPlugin = {
            0: {
                name: 'PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                length: 1
            },
            length: 1
        };
        
        Object.defineProperty(navigator, 'plugins', {
            get: () => mockPlugin
        });
        
        // 模拟 mimeTypes
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => ({
                0: {
                    type: 'application/pdf',
                    description: 'Portable Document Format',
                    suffixes: 'pdf',
                    enabledPlugin: mockPlugin[0]
                },
                length: 1
            })
        });
        """
        await self.session.eval_js(js)
        logger.debug("已模拟插件信息")
    
    async def _mock_device_fingerprint(self):
        """模拟设备指纹（Canvas/WebGL/内存/硬件并发）- 使用动态指纹"""
        # 生成会话专属指纹
        fp_js = self.fingerprint_gen.get_session_js()

        # WebRTC 泄漏防护
        webrtc_js = """
        // WebRTC 泄漏防护
        try {
            const mockRTCPeerConnection = window.RTCPeerConnection;
            window.RTCPeerConnection = function(...args) {
                const pc = new mockRTCPeerConnection(...args);
                const originalCreateOffer = pc.createOffer;
                const originalCreateAnswer = pc.createAnswer;
                pc.createOffer = function(...args) {
                    return originalCreateOffer.apply(pc, args).then(offer => {
                        offer.sdp = offer.sdp.replace(/(c=IN IP4 )\d+\.\d+\.\d+\.\d+/g, '$1127.0.0.1');
                        offer.sdp = offer.sdp.replace(/candidate:(.*?)\s+typ\s+host/g, 'candidate:1 1 UDP 2122252543 127.0.0.1 9 typ host');
                        return offer;
                    });
                };
                pc.createAnswer = function(...args) {
                    return originalCreateAnswer.apply(pc, args).then(answer => {
                        answer.sdp = answer.sdp.replace(/(c=IN IP4 )\d+\.\d+\.\d+\.\d+/g, '$127.0.0.1');
                        answer.sdp = answer.sdp.replace(/candidate:(.*?)\s+typ\s+host/g, 'candidate:1 1 UDP 2122252543 127.0.0.1 9 typ host');
                        return answer;
                    });
                };
                pc.addIceCandidate = function(...args) { return Promise.resolve(); };
                return pc;
            };
            window.RTCPeerConnection.prototype = mockRTCPeerConnection.prototype;
        } catch(e) {}
        """

        js = fp_js + webrtc_js
        await self.session.eval_js(js)
        logger.debug(f"已应用动态设备指纹 (WebGL: {self.fingerprint_gen._session_fingerprint['webgl_vendor']})")
    
    # =========================================================================
    # 人类行为模拟
    # =========================================================================
    
    async def human_like_click(self, x: float, y: float, duration: float = 0.3):
        """模拟人类点击行为"""
        if not self.config.humanize_mouse:
            self.session.send("Input.dispatchMouseEvent", {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1
            })
            self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1
            })
            return
        
        # 模拟鼠标移动轨迹
        steps = int(duration * 20)
        for i in range(steps):
            t = i / steps
            jitter_x = random.uniform(-2, 2)
            jitter_y = random.uniform(-2, 2)
            current_x = x * t + jitter_x
            current_y = y * t + jitter_y
            self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": current_x,
                "y": current_y
            })
            await asyncio.sleep(duration / steps)
        
        # 点击
        self.session.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1
        })
        await asyncio.sleep(random.uniform(0.05, 0.1))
        self.session.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1
        })
    
    async def human_like_type(self, text: str, delay: float = 0.05):
        """模拟人类打字行为"""
        if not self.config.humanize_typing:
            for ch in text:
                self.session.send("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "key": ch,
                    "text": ch
                })
                self.session.send("Input.dispatchKeyEvent", {
                    "type": "char",
                    "text": ch,
                    "unmodifiedText": ch,
                    "key": ch
                })
                self.session.send("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": ch
                })
            return
        
        for ch in text:
            if random.random() < 0.1:
                delay *= random.uniform(2, 5)
            
            self.session.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": ch,
                "text": ch
            })
            await asyncio.sleep(delay * 0.7)
            self.session.send("Input.dispatchKeyEvent", {
                "type": "char",
                "text": ch,
                "unmodifiedText": ch,
                "key": ch
            })
            await asyncio.sleep(delay * 0.3)
            self.session.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": ch
            })
    
    async def human_like_scroll(self, delta_y: int, duration: float = 1.0):
        """模拟人类滚动行为"""
        steps = int(duration * 20)
        for i in range(steps):
            t = i / steps
            current_delta = delta_y * ease_out_quad(t)
            
            self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": 400,
                "y": 300,
                "deltaX": 0,
                "deltaY": current_delta / steps
            })
            
            await asyncio.sleep(duration / steps)
        
        logger.debug(f"人类化滚动: {delta_y}px")
    
    async def random_delay(self, min_delay: float = None, max_delay: float = None):
        """随机延迟（异步非阻塞）"""
        min_sec = min_delay or self.config.random_delay_range[0]
        max_sec = max_delay or self.config.random_delay_range[1]
        
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)  # ✅ 异步非阻塞
        
        logger.debug(f"随机延迟: {delay:.2f}s")
    
    async def random_human_delay(self):
        """随机人类化延迟（0.5-3 秒）"""
        delay = random.uniform(0.5, 3.0)
        await asyncio.sleep(delay)
        logger.debug(f"人类化延迟: {delay:.2f}s")
    
    # =========================================================================
    # 装饰器
    # =========================================================================
    
    def humanized_action(self, func):
        """人类化操作装饰器"""
        async def async_wrapper(*args, **kwargs):
            # 执行前延迟
            await self.random_delay()
            
            # 执行请求
            result = await func(*args, **kwargs)
            
            # 执行后延迟
            await self.random_delay()
            
            return result
        return async_wrapper
    
    async def set_user_agent(self, ua: str):
        """设置 User-Agent（同步 JS 和 HTTP 头）"""
        # 1. 设置 JS 层面的 userAgent
        js = f"""
        Object.defineProperty(navigator, 'userAgent', {{
            get: () => '{ua}'
        }});
        """
        await self.session.eval_js(js)

        # 2. 同步更新 CDP 请求头
        await self.session.set_extra_http_headers({
            'User-Agent': ua
        })

        logger.debug(f"已设置 User-Agent: {ua[:50]}...")

    def get_random_user_agent(self) -> str:
        """生成随机 User-Agent"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        ]
        return random.choice(user_agents)


def ease_out_quad(t: float) -> float:
    """缓动函数：二次EaseOut"""
    return t * (2 - t)
