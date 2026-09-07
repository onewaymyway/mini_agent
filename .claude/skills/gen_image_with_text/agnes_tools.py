from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import List, Optional, Union

import requests

# 支持的档位式 size 与 ratio（Agnes Image 2.5 Flash 文档）。仍然兼容
# "1024x768" 这类历史精确尺寸写法，但不受原生支持的精确尺寸可能会被
# 服务端标准化到最接近的档位。
SUPPORTED_SIZE_TIERS = ("1K", "2K", "3K", "4K")
SUPPORTED_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")

ImageInput = Union[str, List[str]]


class AgnesImageClient:
    ENDPOINT = "https://apihub.agnes-ai.com/v1/images/generations"
    # Agnes Image 2.5 Flash：整体能力全面超过 2.1 Flash，请求/响应参数、
    # 支持尺寸、价格与计费方法均与 2.1 Flash 保持一致，属于同接口下的
    # 直接升级，因此默认模型切到 2.5。如需回退旧模型，构造时传
    # model="agnes-image-2.1-flash" 即可。
    MODEL = "agnes-image-2.5-flash"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: int = 1800,
        max_retries: int = 3,
        verify_ssl: bool = False,
    ):
        self.api_key = api_key
        self.model = model or self.MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl

        self.session = requests.Session()

    # =========================
    # headers
    # =========================
    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # =========================
    # retry request
    # =========================
    def _post(self, payload: dict) -> dict:
        last_err = None

        for i in range(self.max_retries):
            try:
                resp = self.session.post(
                    self.ENDPOINT,
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )

                if not resp.ok:
                    last_err = {
                        "status_code": resp.status_code,
                        "error": resp.text,
                    }
                    time.sleep(1.5 * (i + 1))
                    continue

                return resp.json()

            except Exception as e:
                last_err = str(e)
                time.sleep(1.5 * (i + 1))

        return {
            "success": False,
            "error": last_err,
        }

    # =========================
    # utils: save image
    # =========================
    def _save_image_from_url(self, url: str, save_path: str):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        r = requests.get(url, timeout=60, verify=False)
        r.raise_for_status()

        with open(save_path, "wb") as f:
            f.write(r.content)

    def _save_image_from_base64(self, b64: str, save_path: str):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        image_bytes = base64.b64decode(b64)

        with open(save_path, "wb") as f:
            f.write(image_bytes)

    # =========================
    # utils: normalize image input(s) to URL / Data URI
    # =========================
    def _normalize_images(self, image: ImageInput) -> List[str]:
        """把单张或多张输入图片统一转换成 API 需要的
        `extra_body.image` 数组（每项是公共 URL 或 Data URI Base64）。
        本地文件路径会被转成 Data URI；已经是 http(s) URL 的原样保留。
        """
        items = [image] if isinstance(image, str) else list(image)
        normalized = []
        for item in items:
            if item.startswith("http://") or item.startswith("https://") or item.startswith("data:"):
                normalized.append(item)
            else:
                normalized.append(self.file_to_data_uri(item))
        return normalized

    # =========================
    # low-level API
    # =========================
    def text_to_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        ratio: Optional[str] = None,
        response_format: str = "url",
        save_path: Optional[str] = None,
    ) -> dict:
        """文生图。

        Args:
            prompt: 图像生成的文本描述。
            size: 输出尺寸。推荐使用档位式 "1K"/"2K"/"3K"/"4K"（配合
                `ratio` 使用），也兼容 "1024x768" 这类历史精确尺寸写法，
                但不受原生支持的精确尺寸可能会被标准化到最接近的档位。
            ratio: 与档位式 size 配合使用的宽高比，支持 "1:1"、"3:4"、
                "4:3"、"16:9"、"9:16"、"2:3"、"3:2"、"21:9"。仅在 size
                为档位值（1K/2K/3K/4K）时才有意义。
            response_format: "url" 或 "b64_json"，通过 `extra_body`
                传递给 API（注意：response_format 不能放在请求体顶层）。
            save_path: 若提供，会把生成的图片保存到本地。
        """

        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "extra_body": {
                "response_format": response_format,
            },
        }
        if ratio:
            payload["ratio"] = ratio

        result = self._post(payload)

        if not result.get("data"):
            return result

        print("result:", result)

        data = result["data"][0]

        if response_format == "url":
            url = data["url"]

            if save_path:
                self._save_image_from_url(url, save_path)

            return result

        if response_format == "b64_json":
            b64 = data["b64_json"]

            if save_path:
                self._save_image_from_base64(b64, save_path)

            return result

        return result

    def image_to_image(
        self,
        image: ImageInput,
        prompt: str,
        size: str = "1024x1024",
        ratio: Optional[str] = None,
        response_format: str = "url",
        save_path: Optional[str] = None,
    ) -> dict:
        """图生图 / 多图合成。

        Args:
            image: 单张图片路径/URL，或多张图片组成的列表（用于多图
                合成工作流）。本地文件路径会自动转换成 Data URI；
                公共 http(s) URL 原样传递。
            prompt: 描述如何编辑/合成图片的文本。
            size: 同 `text_to_image`。
            ratio: 同 `text_to_image`。
            response_format: "url" 或 "b64_json"。
            save_path: 若提供，会把结果图片保存到本地。

        注意：图生图 / 多图合成不需要传递 `tags: ["img2img"]`，
        `extra_body.image` 里放输入图像即可。
        """

        images = self._normalize_images(image)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "extra_body": {
                "image": images,
                "response_format": response_format,
            },
        }
        if ratio:
            payload["ratio"] = ratio

        result = self._post(payload)

        if not result.get("data"):
            return result

        print("result:", result)

        data = result["data"][0]

        if response_format == "url":
            url = data["url"]

            if save_path:
                self._save_image_from_url(url, save_path)

            return result

        if response_format == "b64_json":
            b64 = data["b64_json"]

            if save_path:
                self._save_image_from_base64(b64, save_path)

            return result

        return result

    # =========================
    # high-level API (agent friendly)
    # =========================
    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        ratio: Optional[str] = None,
        save_path: Optional[str] = None,
    ) -> dict:

        result = self.text_to_image(
            prompt=prompt,
            size=size,
            ratio=ratio,
            response_format="url",
            save_path=save_path,
        )

        if not result.get("data"):
            return {
                "success": False,
                "error": result,
            }

        return {
            "success": True,
            "image_url": result["data"][0]["url"],
            "save_path": save_path,
        }

    def edit_image(
        self,
        image_url: ImageInput,
        prompt: str,
        size: str = "1024x1024",
        ratio: Optional[str] = None,
        save_path: Optional[str] = None,
    ) -> dict:
        """基于一张或多张参考图片进行编辑/合成。`image_url` 传列表即为
        多图合成（例如把第一张图当主角色、第二张图当产品参考）。"""

        result = self.image_to_image(
            image=image_url,
            prompt=prompt,
            size=size,
            ratio=ratio,
            response_format="url",
            save_path=save_path,
        )

        if not result.get("data"):
            return {
                "success": False,
                "error": result,
            }

        return {
            "success": True,
            "image_url": result["data"][0]["url"],
            "save_path": save_path,
        }

    def compose_images(
        self,
        images: List[str],
        prompt: str,
        size: str = "1024x1024",
        ratio: Optional[str] = None,
        save_path: Optional[str] = None,
    ) -> dict:
        """多图合成的语义化别名：把多张参考图组合成一张新图。等价于
        `edit_image(image_url=images, ...)`，只是名字更贴合"多图合成"
        这个使用场景，避免调用方误以为只能传一张图。"""
        return self.edit_image(
            image_url=images,
            prompt=prompt,
            size=size,
            ratio=ratio,
            save_path=save_path,
        )

    # =========================
    # file utils
    # =========================
    @staticmethod
    def file_to_data_uri(image_path: str) -> str:
        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        encoded = base64.b64encode(image_bytes).decode()

        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def save_base64_image(b64: str, output_path: str):
        image_bytes = base64.b64decode(b64)

        with open(output_path, "wb") as f:
            f.write(image_bytes)
