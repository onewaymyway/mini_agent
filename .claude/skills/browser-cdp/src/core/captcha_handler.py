"""
captcha_handler.py - 验证码处理模块

处理常见验证码类型：
- 滑块验证码
- 点选验证码
- 图形验证码（OCR）
- 短信/邮箱验证码
- 人机验证（reCAPTCHA、hCaptcha 等）
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, List, Callable

from PIL import Image

logger = logging.getLogger(__name__)


class CaptchaType(Enum):
    """验证码类型"""
    SLIDER = "slider"           # 滑块验证码
    CLICK = "click"             # 点选验证码
    TEXT = "text"               # 文字验证码（OCR）
    SMS = "sms"                 # 短信验证码
    EMAIL = "email"             # 邮箱验证码
    RECAPTCHA = "recaptcha"     # reCAPTCHA
    HCAPTCHA = "hcaptcha"       # hCaptcha
    GEOGUESSER = "geoguesser"   # 地理猜测验证码
    UNKNOWN = "unknown"         # 未知类型


@dataclass
class CaptchaResult:
    """验证码处理结果"""
    success: bool
    captcha_type: CaptchaType
    solution: Optional[dict] = None
    message: str = ""
    
    def __str__(self):
        status = "成功" if self.success else "失败"
        return f"[{status}] 类型={self.captcha_type.value}, 消息={self.message}"


class CaptchaHandler:
    """
    验证码处理器
    
    支持多种验证码类型的检测和自动处理
    """
    
    # 常见验证码选择器
    SELECTORS = {
        CaptchaType.SLIDER: [
            "#slideBlock",
            ".slide-block",
            "[class*='slider']",
            "[class*='slide']",
            "#nc_1_wrapper",
            ".nc_wrapper",
            "[class*='geetest']",
        ],
        CaptchaType.CLICK: [
            "[class*='point']",
            "[class*='click']",
            "[class*='choose']",
            ".verify-code-click",
        ],
        CaptchaType.TEXT: [
            "[class*='captcha'] img",
            "[class*='verify'] img",
            "#captchaImg",
            ".captcha-img",
        ],
        CaptchaType.RECAPTCHA: [
            "[class*='recaptcha']",
            "#recaptcha",
            "iframe[src*='recaptcha']",
        ],
        CaptchaType.HCAPTCHA: [
            "[class*='hcaptcha']",
            "#hcaptcha",
            "iframe[src*='hcaptcha']",
        ],
    }
    
    # 验证码检测关键词
    DETECTION_PATTERNS = {
        CaptchaType.SLIDER: [
            r"滑动.*验证", r"slider.*captcha", r"拖拽.*滑块",
            r"geetest", r"captcha.*slide", r"slide.*block"
        ],
        CaptchaType.CLICK: [
            r"点选.*验证", r"click.*captcha", r"选择.*正确",
            r"point.*captcha", r"captcha.*click"
        ],
        CaptchaType.TEXT: [
            r"输入.*验证码", r"text.*captcha", r"captcha.*text",
            r"识别.*图片", r"captcha.*img"
        ],
        CaptchaType.RECAPTCHA: [
            r"recaptcha", r"i'm not a robot", r"google.*captcha"
        ],
        CaptchaType.HCAPTCHA: [
            r"hcaptcha", r"verify.*human"
        ],
    }
    
    def __init__(self, session, ocr_api: Optional[Callable] = None):
        """
        Args:
            session: CDP session 对象
            ocr_api: OCR API 函数（可选），接收图片字节，返回文本
        """
        self.session = session
        self.ocr_api = ocr_api
        self._captcha_cache: dict = {}
    
    async def detect_captcha(self) -> CaptchaType:
        """
        检测当前页面的验证码类型
        
        Returns:
            CaptchaType: 检测到的验证码类型
        """
        # 1. 检查 URL 中的验证码标识
        url = await self.session.get_current_url()
        if 'captcha' in url.lower() or 'verify' in url.lower():
            logger.debug(f"URL 包含验证码标识: {url}")
        
        # 2. 检查页面中的验证码元素
        for captcha_type, selectors in self.SELECTORS.items():
            for selector in selectors:
                try:
                    elements = await self.session.query_selector_all(selector)
                    if elements:
                        logger.info(f"检测到验证码元素: {captcha_type.value}, selector={selector}")
                        return captcha_type
                except Exception as e:
                    logger.debug(f"检查 {selector} 失败: {e}")
        
        # 3. 检查页面文本中的验证码关键词
        page_text = await self.session.get_page_text()
        for captcha_type, patterns in self.DETECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, page_text, re.IGNORECASE):
                    logger.info(f"页面文本匹配验证码模式: {captcha_type.value}, pattern={pattern}")
                    return captcha_type
        
        # 4. 检查 iframe 中的验证码
        try:
            iframes = await self.session.query_selector_all("iframe")
            for iframe in iframes:
                iframe_src = await iframe.get_attribute("src")
                if iframe_src and any(kw in iframe_src.lower() for kw in ['captcha', 'verify', 'recaptcha', 'hcaptcha', 'geetest']):
                    captcha_type = self._infer_type_from_src(iframe_src)
                    logger.info(f"iframe 中包含验证码: {captcha_type.value}")
                    return captcha_type
        except Exception as e:
            logger.debug(f"检查 iframe 失败: {e}")
        
        return CaptchaType.UNKNOWN
    
    def _infer_type_from_src(self, src: str) -> CaptchaType:
        """从 iframe src 推断验证码类型"""
        src_lower = src.lower()
        if 'recaptcha' in src_lower:
            return CaptchaType.RECAPTCHA
        elif 'hcaptcha' in src_lower:
            return CaptchaType.HCAPTCHA
        elif 'geetest' in src_lower or 'slide' in src_lower:
            return CaptchaType.SLIDER
        elif 'click' in src_lower or 'point' in src_lower:
            return CaptchaType.CLICK
        return CaptchaType.UNKNOWN
    
    async def handle_captcha(self) -> CaptchaResult:
        """
        处理当前页面的验证码
        
        Returns:
            CaptchaResult: 处理结果
        """
        captcha_type = await self.detect_captcha()
        
        if captcha_type == CaptchaType.UNKNOWN:
            return CaptchaResult(
                success=False,
                captcha_type=captcha_type,
                message="未检测到验证码"
            )
        
        logger.info(f"开始处理验证码: {captcha_type.value}")
        
        # 根据类型分发处理
        handlers = {
            CaptchaType.SLIDER: self._handle_slider,
            CaptchaType.CLICK: self._handle_click,
            CaptchaType.TEXT: self._handle_text,
            CaptchaType.RECAPTCHA: self._handle_recaptcha,
            CaptchaType.HCAPTCHA: self._handle_hcaptcha,
            CaptchaType.SMS: self._handle_sms,
            CaptchaType.EMAIL: self._handle_email,
        }
        
        handler = handlers.get(captcha_type, self._handle_unknown)
        return await handler()
    
    async def _handle_slider(self) -> CaptchaResult:
        """处理滑块验证码"""
        try:
            # 1. 找到滑块元素
            slider = await self.session.query_selector("#slideBlock") or \
                     await self.session.query_selector(".slide-block") or \
                     await self.session.query_selector("[class*='slider']")
            
            if not slider:
                return CaptchaResult(
                    success=False,
                    captcha_type=CaptchaType.SLIDER,
                    message="未找到滑块元素"
                )
            
            # 2. 获取滑块位置
            slider_rect = await slider.bounding_box()
            start_x = slider_rect['x']
            start_y = slider_rect['y']
            
            # 3. 计算滑动距离（通过对比缺口图）
            distance = await self._calculate_slide_distance()
            
            # 4. 执行滑动
            await self._slide(slider, distance)
            
            return CaptchaResult(
                success=True,
                captcha_type=CaptchaType.SLIDER,
                solution={"distance": distance},
                message=f"滑块滑动距离: {distance}px"
            )
            
        except Exception as e:
            logger.error(f"滑块验证码处理失败: {e}")
            return CaptchaResult(
                success=False,
                captcha_type=CaptchaType.SLIDER,
                message=f"处理失败: {str(e)}"
            )
    
    async def _calculate_slide_distance(self) -> float:
        """计算滑动距离（通过图像对比）"""
        try:
            # 获取完整图片和缺口图片
            full_img = await self._get_captcha_image("full")
            gap_img = await self._get_captcha_image("gap")
            
            if not full_img or not gap_img:
                logger.warning("无法获取验证码图片，使用默认距离")
                return 280.0
            
            # 图像对比计算缺口位置
            distance = self._compare_images(full_img, gap_img)
            
            if distance > 0:
                logger.info(f"图像对比计算滑动距离: {distance}px")
                return float(distance)
            
        except Exception as e:
            logger.error(f"图像对比失败: {e}，使用默认距离")
        
        # 降级：返回默认值
        return 280.0
    
    async def _get_captcha_image(self, img_type: str) -> Optional[Image.Image]:
        """获取验证码图片"""
        try:
            if img_type == "full":
                selector = "[class*='geetest'] canvas, [class*='captcha'] canvas, .gt_box_wrap canvas"
            else:
                selector = "[class*='geetest'] img[src*='bg'], [class*='captcha'] img[src*='bg']"
            
            elem = await self.session.query_selector(selector)
            if elem:
                # 获取图片数据
                src = await elem.get_attribute("src")
                if src and src.startswith("data:"):
                    # Base64 编码的图片
                    import base64
                    data = src.split(",")[1]
                    img_data = base64.b64decode(data)
                    return Image.open(io.BytesIO(img_data))
        except Exception as e:
            logger.debug(f"获取验证码图片失败: {e}")
        return None
    
    def _compare_images(self, full_img: Image.Image, gap_img: Image.Image) -> int:
        """通过图像对比计算缺口位置"""
        try:
            # 转换为灰度图
            full_gray = full_img.convert('L')
            gap_gray = gap_img.convert('L')
            
            # 获取图片尺寸
            full_width, full_height = full_gray.size
            gap_width, gap_height = gap_gray.size
            
            # 逐像素对比
            full_pixels = list(full_gray.getdata())
            gap_pixels = list(gap_gray.getdata())
            
            # 计算缺口位置（简化版：找第一个显著差异的列）
            threshold = 30  # 像素差异阈值
            
            for x in range(gap_width):
                diff_count = 0
                for y in range(min(gap_height, full_height)):
                    full_idx = y * full_width + x
                    gap_idx = y * gap_width + x
                    
                    if full_idx < len(full_pixels) and gap_idx < len(gap_pixels):
                        if abs(full_pixels[full_idx] - gap_pixels[gap_idx]) > threshold:
                            diff_count += 1
                
                # 如果这一列有显著差异，认为是缺口位置
                if diff_count > gap_height * 0.5:
                    return x
            
            # 如果找不到，返回图片宽度
            return gap_width
            
        except Exception as e:
            logger.error(f"图像对比计算失败: {e}")
            return -1
    
    async def _slide(self, slider, distance: float):
        """执行滑动操作"""
        # 获取滑块起始位置
        rect = await slider.bounding_box()
        start_x = rect['x'] + rect['width'] / 2
        start_y = rect['y'] + rect['height'] / 2
        
        # 模拟人类滑动轨迹
        steps = 30
        for i in range(steps + 1):
            t = i / steps
            # 缓动函数
            ease_t = 1 - pow(1 - t, 3)
            current_x = start_x + distance * ease_t
            
            await self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": current_x,
                "y": start_y
            })
            await asyncio.sleep(0.02)
        
        # 按下、移动、释放
        await self.session.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": start_x,
            "y": start_y,
            "button": "left"
        })
        await asyncio.sleep(0.1)
        
        for i in range(steps + 1):
            t = i / steps
            ease_t = 1 - pow(1 - t, 3)
            current_x = start_x + distance * ease_t
            
            await self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": current_x,
                "y": start_y
            })
            await asyncio.sleep(0.02)
        
        await asyncio.sleep(0.1)
        await self.session.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": start_x + distance,
            "y": start_y,
            "button": "left"
        })
    
    async def _handle_click(self) -> CaptchaResult:
        """处理点选验证码"""
        try:
            # 1. 获取指令文本
            instruction = await self._get_click_instruction()
            
            # 2. 找到需要点击的元素
            targets = await self.session.query_selector_all("[class*='target']")
            
            if not targets:
                return CaptchaResult(
                    success=False,
                    captcha_type=CaptchaType.CLICK,
                    message="未找到点击目标"
                )
            
            # 3. 根据指令点击对应元素
            for target in targets:
                text = await target.inner_text()
                if instruction in text or text in instruction:
                    rect = await target.bounding_box()
                    await self.session.click(rect['x'] + rect['width']/2, rect['y'] + rect['height']/2)
                    return CaptchaResult(
                        success=True,
                        captcha_type=CaptchaType.CLICK,
                        solution={"target": text},
                        message=f"已点击: {text}"
                    )
            
            # 4. 如果无法识别，点击第一个元素
            rect = await targets[0].bounding_box()
            await self.session.click(rect['x'] + rect['width']/2, rect['y'] + rect['height']/2)
            return CaptchaResult(
                success=True,
                captcha_type=CaptchaType.CLICK,
                solution={"target": "first"},
                message="已点击第一个元素"
            )
            
        except Exception as e:
            logger.error(f"点选验证码处理失败: {e}")
            return CaptchaResult(
                success=False,
                captcha_type=CaptchaType.CLICK,
                message=f"处理失败: {str(e)}"
            )
    
    async def _get_click_instruction(self) -> str:
        """获取点选验证码的指令文本"""
        # 尝试从页面获取指令
        selectors = [
            "[class*='instruction']",
            "[class*='command']",
            "[class*='hint']",
            ".captcha-instruction",
        ]
        for selector in selectors:
            try:
                elem = await self.session.query_selector(selector)
                if elem:
                    return await elem.inner_text()
            except:
                continue
        return ""
    
    async def _handle_text(self) -> CaptchaResult:
        """处理文字验证码（OCR）"""
        try:
            # 1. 获取验证码图片
            img_elem = await self.session.query_selector("[class*='captcha'] img") or \
                       await self.session.query_selector(".captcha-img")
            
            if not img_elem:
                return CaptchaResult(
                    success=False,
                    captcha_type=CaptchaType.TEXT,
                    message="未找到验证码图片"
                )
            
            # 2. 截图验证码区域
            rect = await img_elem.bounding_box()
            screenshot = await self.session.screenshot(
                clip={
                    "x": rect['x'],
                    "y": rect['y'],
                    "width": rect['width'],
                    "height": rect['height']
                }
            )
            
            # 3. OCR 识别
            if self.ocr_api:
                text = self.ocr_api(screenshot)
            else:
                text = await self._ocr_via_api(screenshot)
            
            if not text:
                return CaptchaResult(
                    success=False,
                    captcha_type=CaptchaType.TEXT,
                    message="OCR 识别失败"
                )
            
            # 4. 输入识别结果
            input_elem = await self.session.query_selector("input[type='text']")
            if input_elem:
                await input_elem.click()
                await input_elem.type(text)
            
            return CaptchaResult(
                success=True,
                captcha_type=CaptchaType.TEXT,
                solution={"text": text},
                message=f"OCR 识别结果: {text}"
            )
            
        except Exception as e:
            logger.error(f"文字验证码处理失败: {e}")
            return CaptchaResult(
                success=False,
                captcha_type=CaptchaType.TEXT,
                message=f"处理失败: {str(e)}"
            )
    
    async def _ocr_via_api(self, image_bytes: bytes) -> str:
        """通过外部 API 进行 OCR 识别"""
        # 尝试使用配置的 OCR API
        if self.ocr_api:
            try:
                text = self.ocr_api(image_bytes)
                if text:
                    logger.info("OCR 识别成功")
                    return text
            except Exception as e:
                logger.error(f"OCR API 调用失败: {e}")
        
        # 尝试使用环境变量配置的 OCR 服务
        ocr_service = os.environ.get("BROWSER_CDP_OCR_SERVICE", "")
        if ocr_service == "baidu":
            return await self._baidu_ocr(image_bytes)
        elif ocr_service == "tencent":
            return await self._tencent_ocr(image_bytes)
        elif ocr_service == "aliyun":
            return await self._aliyun_ocr(image_bytes)
        
        logger.warning("未配置 OCR API，需要手动输入验证码")
        return ""
    
    async def _baidu_ocr(self, image_bytes: bytes) -> str:
        """百度 OCR API"""
        try:
            import requests
            access_token = os.environ.get("BAIDU_OCR_ACCESS_TOKEN", "")
            if not access_token:
                logger.warning("未配置 BAIDU_OCR_ACCESS_TOKEN")
                return ""
            
            url = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
            params = {"access_token": access_token}
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            
            # 图片转 base64
            img_base64 = base64.b64encode(image_bytes).decode('utf-8')
            data = {"image": img_base64}
            
            response = requests.post(url, params=params, headers=headers, data=data, timeout=10)
            result = response.json()
            
            if result.get("words_result"):
                text = "".join([item.get("words", "") for item in result["words_result"]])
                logger.info(f"百度 OCR 识别成功: {text}")
                return text
            return ""
        except Exception as e:
            logger.error(f"百度 OCR 失败: {e}")
            return ""
    
    async def _tencent_ocr(self, image_bytes: bytes) -> str:
        """腾讯 OCR API"""
        try:
            import requests
            secret_id = os.environ.get("TENCENT_CLOUD_SECRET_ID", "")
            secret_key = os.environ.get("TENCENT_CLOUD_SECRET_KEY", "")
            if not secret_id or not secret_key:
                logger.warning("未配置腾讯 OCR 凭证")
                return ""
            
            # 简化实现：实际需要使用 qcloud-sdk
            logger.warning("腾讯 OCR 需要配置 qcloud-sdk")
            return ""
        except Exception as e:
            logger.error(f"腾讯 OCR 失败: {e}")
            return ""
    
    async def _aliyun_ocr(self, image_bytes: bytes) -> str:
        """阿里云 OCR API"""
        try:
            import requests
            access_key_id = os.environ.get("ALIYUN_ACCESS_KEY_ID", "")
            access_key_secret = os.environ.get("ALIYUN_ACCESS_KEY_SECRET", "")
            if not access_key_id or not access_key_secret:
                logger.warning("未配置阿里云 OCR 凭证")
                return ""
            
            # 简化实现：实际需要使用 aliyun-sdk
            logger.warning("阿里云 OCR 需要配置 aliyun-sdk")
            return ""
        except Exception as e:
            logger.error(f"阿里云 OCR 失败: {e}")
            return ""
    
    async def _handle_recaptcha(self) -> CaptchaResult:
        """处理 reCAPTCHA v2/v3"""
        try:
            # 尝试获取 reCAPTCHA token
            token = await self.session.eval_js("""
                () => {
                    if (typeof grecaptcha !== 'undefined') {
                        const siteKey = document.querySelector('.g-recaptcha')?.getAttribute('data-sitekey');
                        if (siteKey) {
                            return grecaptcha.getResponse();
                        }
                    }
                    // reCAPTCHA v3
                    if (typeof recaptchaV3 !== 'undefined') {
                        return recaptchaV3;
                    }
                    return '';
                }
            """)
            if token and len(str(token)) > 10:
                return CaptchaResult(
                    success=True,
                    captcha_type=CaptchaType.RECAPTCHA,
                    solution={'token': str(token)},
                    message="reCAPTCHA token 获取成功"
                )
        except Exception as e:
            logger.debug(f"reCAPTCHA token 获取失败: {e}")
        
        # 尝试自动点击 reCAPTCHA 复选框
        try:
            checkbox = await self.session.find_element(
                "iframe[src*='recaptcha']", wait_timeout=3
            )
            if checkbox:
                await self.session.click_element(checkbox, retry=3)
                return CaptchaResult(
                    success=True,
                    captcha_type=CaptchaType.RECAPTCHA,
                    solution={'method': 'auto_click'},
                    message="已尝试自动点击 reCAPTCHA"
                )
        except Exception as e:
            logger.debug(f"reCAPTCHA 自动点击失败: {e}")
        
        return CaptchaResult(
            success=False,
            captcha_type=CaptchaType.RECAPTCHA,
            message="reCAPTCHA 需要手动处理或使用第三方服务（2Captcha/9Captcha）"
        )
    
    async def _handle_hcaptcha(self) -> CaptchaResult:
        """处理 hCaptcha"""
        try:
            # 尝试获取 hCaptcha token
            token = await self.session.eval_js("""
                () => {
                    if (typeof hcaptcha !== 'undefined') {
                        return hcaptcha.getResponse();
                    }
                    return '';
                }
            """)
            if token and len(str(token)) > 10:
                return CaptchaResult(
                    success=True,
                    captcha_type=CaptchaType.HCAPTCHA,
                    solution={'token': str(token)},
                    message="hCaptcha token 获取成功"
                )
        except Exception as e:
            logger.debug(f"hCaptcha token 获取失败: {e}")
        
        return CaptchaResult(
            success=False,
            captcha_type=CaptchaType.HCAPTCHA,
            message="hCaptcha 需要手动处理或使用第三方服务"
        )
    
    async def _handle_sms(self) -> CaptchaResult:
        """处理短信验证码"""
        return CaptchaResult(
            success=False,
            captcha_type=CaptchaType.SMS,
            message="短信验证码需要用户提供"
        )
    
    async def _handle_email(self) -> CaptchaResult:
        """处理邮箱验证码"""
        return CaptchaResult(
            success=False,
            captcha_type=CaptchaType.EMAIL,
            message="邮箱验证码需要用户提供"
        )
    
    async def _handle_unknown(self) -> CaptchaResult:
        """处理未知类型验证码"""
        return CaptchaResult(
            success=False,
            captcha_type=CaptchaType.UNKNOWN,
            message="未知验证码类型，需要手动处理"
        )
    
    def request_manual_input(self, captcha_type: CaptchaType, context: str = "") -> Optional[str]:
        """
        请求用户手动输入验证码
        
        Args:
            captcha_type: 验证码类型
            context: 额外上下文信息
        
        Returns:
            用户输入的验证码文本，或 None（用户取消）
        """
        type_names = {
            CaptchaType.SLIDER: "滑块验证码",
            CaptchaType.CLICK: "点选验证码",
            CaptchaType.TEXT: "文字验证码",
            CaptchaType.SMS: "短信验证码",
            CaptchaType.EMAIL: "邮箱验证码",
            CaptchaType.RECAPTCHA: "reCAPTCHA",
            CaptchaType.HCAPTCHA: "hCaptcha",
        }
        
        name = type_names.get(captcha_type, "验证码")
        prompt = f"检测到 {name}，请手动完成验证。"
        if context:
            prompt += f" 上下文: {context}"
        
        logger.warning(prompt)
        # 在实际应用中，这里会弹出 UI 让用户输入
        # 当前返回 None，表示需要手动处理
        return None


class AntiDetection:
    """
    反检测管理器
    
    管理请求头、指纹、行为模式等反检测策略
    """
    
    # 常见反检测头
    DEFAULT_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    
    def __init__(self, session):
        self.session = session
        self._headers = dict(self.DEFAULT_HEADERS)
        self._user_agent = None
        self._viewport = None
        self._platform = None
    
    def set_user_agent(self, ua: str):
        """设置 User-Agent"""
        self._user_agent = ua
        self._headers["User-Agent"] = ua
    
    def set_viewport(self, width: int, height: int):
        """设置视口大小"""
        self._viewport = (width, height)
    
    def set_platform(self, platform: str):
        """设置平台信息"""
        self._platform = platform
    
    async def apply(self):
        """应用所有反检测设置"""
        # 设置 User-Agent
        if self._user_agent:
            await self.session.set_user_agent(self._user_agent)
        
        # 设置视口
        if self._viewport:
            await self.session.set_viewport(self._viewport[0], self._viewport[1])
        
        # 应用 stealth 脚本
        await self._apply_stealth()
    
    async def _apply_stealth(self):
        """应用 stealth 脚本"""
        js = """
        // 移除 webdriver 属性
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // 模拟 Chrome runtime
        window.chrome = {
            runtime: {
                connect: () => ({}),
                sendMessage: () => Promise.resolve()
            }
        };
        
        // 模拟 plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [{}
                { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
                { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
                { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' }
            ]
        });
        
        // 模拟 languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en-US', 'en']
        });
        """
        await self.session.eval_js(js)
        logger.debug("已应用 stealth 脚本")
    
    def get_headers(self) -> dict:
        """获取当前请求头"""
        return dict(self._headers)


# 便捷函数
async def detect_and_handle_captcha(session, ocr_api=None) -> CaptchaResult:
    """
    检测并处理验证码的便捷函数
    
    Args:
        session: CDP session 对象
        ocr_api: OCR API 函数（可选）
    
    Returns:
        CaptchaResult: 处理结果
    """
    handler = CaptchaHandler(session, ocr_api)
    return await handler.handle_captcha()


async def apply_anti_detection(session, user_agent: str = None, viewport: Tuple[int, int] = None):
    """
    应用反检测设置的便捷函数
    
    Args:
        session: CDP session 对象
        user_agent: 用户代理字符串（可选）
        viewport: 视口尺寸 (width, height)（可选）
    """
    anti = AntiDetection(session)
    if user_agent:
        anti.set_user_agent(user_agent)
    if viewport:
        anti.set_viewport(*viewport)
    await anti.apply()
