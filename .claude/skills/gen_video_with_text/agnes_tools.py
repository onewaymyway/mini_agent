from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Optional, List

import requests

from agnes_key_pool import AgnesKeyPool, is_rate_limit_error


class AgnesVideoClient:
    BASE_URL = "https://apihub.agnes-ai.com/v1"
    QUERY_URL = "https://apihub.agnes-ai.com/agnesapi"
    MODEL = "agnes-video-2.5-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        key_pool: Optional[AgnesKeyPool] = None,
        timeout: int = 1800,
        max_retries: int = 3,
        verify_ssl: bool = False,
    ):
        """
        Args:
            api_key: 单个 API key（向后兼容，不带自动切换）。
            key_pool: 多 key 池；若提供，遇到限流会自动切换 key，
                优先级高于 api_key（会覆盖初始 self.api_key）。
        """
        if not api_key and not key_pool:
            raise ValueError("Either 'api_key' or 'key_pool' must be provided")

        self.key_pool = key_pool
        self.api_key = key_pool.acquire() if key_pool else api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl

        self.session = requests.Session()

    # =========================
    # file utils
    # =========================
    @staticmethod
    def _is_local_path(value: str) -> bool:
        """判断一个字符串是不是本地文件路径（而不是 http(s) URL 或已经是
        data: URI）。Agnes 接口的 images/first_frame/last_frame 只接受
        公网 http(s) URL 或 base64 data URI，不支持本地路径，这里统一
        识别后转换，避免出现
        'media must be a public http(s) URL or base64 data' 报错。"""
        if not value:
            return False
        lowered = value.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            return False
        if lowered.startswith("data:"):
            return False
        return True

    @staticmethod
    def file_to_data_uri(image_path: str) -> str:
        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        encoded = base64.b64encode(image_bytes).decode()

        return f"data:{mime_type};base64,{encoded}"

    @classmethod
    def _normalize_media(cls, value: Optional[str]) -> Optional[str]:
        """把单个 first_frame/last_frame 值中的本地路径转换成 data URI；
        http(s) URL 或已经是 data URI 的值原样返回。"""
        if not value:
            return value
        if cls._is_local_path(value):
            return cls.file_to_data_uri(value)
        return value

    @classmethod
    def _normalize_media_list(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        """对 images 等列表参数逐个做本地路径 -> data URI 转换。"""
        if not values:
            return values
        return [cls._normalize_media(v) for v in values]

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
    def _post(self, url: str, payload: dict) -> dict:
        """带重试的 POST，限流自动切换 key（详见 _get 上的说明，逻辑一致）。"""
        last_err = None
        attempt = 0

        while attempt < self.max_retries:
            if self.key_pool:
                self.api_key = self.key_pool.acquire()

            try:
                resp = self.session.post(
                    url,
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

                    if self.key_pool and is_rate_limit_error(resp.status_code, resp.text):
                        next_key = self.key_pool.report_rate_limit(self.api_key)
                        if next_key:
                            self.api_key = next_key
                            continue  # 换 key 立即重试，不计入 attempt

                    attempt += 1
                    time.sleep(1.5 * attempt)
                    continue

                if self.key_pool:
                    self.key_pool.report_success(self.api_key)

                return resp.json()

            except Exception as e:
                last_err = str(e)
                attempt += 1
                time.sleep(1.5 * attempt)

        return {
            "success": False,
            "error": last_err,
        }

    def _get(self, url: str, params: dict) -> dict:
        """带重试的 GET（任务轮询也可能被限流），限流自动切换 key：
        遇到 429 / 限流关键字时，若 key_pool 里还有可用 key，立即换 key
        重试且不计入 max_retries；非限流错误走原有 sleep-and-retry。"""
        last_err = None
        attempt = 0

        while attempt < self.max_retries:
            if self.key_pool:
                self.api_key = self.key_pool.acquire()

            try:
                resp = self.session.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=60,
                    verify=self.verify_ssl,
                )

                if not resp.ok:
                    last_err = {
                        "status_code": resp.status_code,
                        "error": resp.text,
                    }

                    if self.key_pool and is_rate_limit_error(resp.status_code, resp.text):
                        next_key = self.key_pool.report_rate_limit(self.api_key)
                        if next_key:
                            self.api_key = next_key
                            continue  # 换 key 立即重试，不计入 attempt

                    attempt += 1
                    time.sleep(1.5 * attempt)
                    continue

                if self.key_pool:
                    self.key_pool.report_success(self.api_key)

                return resp.json()

            except Exception as e:
                last_err = str(e)
                attempt += 1
                time.sleep(1.5 * attempt)

        return {
            "success": False,
            "error": last_err,
        }

    # =========================
    # utils: save video
    # =========================
    def _save_video_from_url(self, url: str, save_path: str):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        r = requests.get(url, timeout=120, verify=False, stream=True)
        r.raise_for_status()

        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    # =========================
    # low-level API: create task
    # =========================
    def create_video(
        self,
        prompt: str,
        mode: str = "text",
        seconds: str = "5",
        size: str = "720P",
        aspect_ratio: str = "16:9",
        first_frame: Optional[str] = None,
        last_frame: Optional[str] = None,
        images: Optional[List[str]] = None,
        audios: Optional[List[str]] = None,
        seed: Optional[int] = None,
    ) -> dict:
        """Create a video generation task. Returns the raw task creation response."""

        # images / first_frame / last_frame 只接受 http(s) URL 或 base64
        # data URI，本地文件路径会被 API 直接 400（见 SKILL 报障记录），
        # 这里统一做一次自动转换，调用方传本地路径也能正常工作。
        first_frame = self._normalize_media(first_frame)
        last_frame = self._normalize_media(last_frame)
        images = self._normalize_media_list(images)

        payload = {
            "model": self.MODEL,
            "prompt": prompt,
            "mode": mode,
            "seconds": seconds,
            "size": size,
            "aspect_ratio": aspect_ratio,
        }

        if mode == "keyframe":
            if first_frame:
                payload["first_frame"] = first_frame
            if last_frame:
                payload["last_frame"] = last_frame

        if mode == "reference":
            if images:
                payload["images"] = images
            if audios:
                payload["audios"] = audios

        if seed is not None:
            payload["seed"] = seed

        return self._post(f"{self.BASE_URL}/videos", payload)

    # =========================
    # low-level API: query task
    # =========================
    def query_video(self, video_id: str) -> dict:
        return self._get(
            self.QUERY_URL,
            {"video_id": video_id, "model_name": self.MODEL},
        )

    # =========================
    # high-level API (agent friendly)
    # =========================
    def generate_video(
        self,
        prompt: str,
        mode: str = "text",
        seconds: str = "5",
        size: str = "720P",
        aspect_ratio: str = "16:9",
        first_frame: Optional[str] = None,
        last_frame: Optional[str] = None,
        images: Optional[List[str]] = None,
        audios: Optional[List[str]] = None,
        seed: Optional[int] = None,
        save_path: Optional[str] = None,
        poll_interval: float = 2.0,
        max_wait_seconds: int = 1800,
    ) -> dict:
        """Create a video task and poll until completed/failed, optionally saving the result."""

        create_result = self.create_video(
            prompt=prompt,
            mode=mode,
            seconds=seconds,
            size=size,
            aspect_ratio=aspect_ratio,
            first_frame=first_frame,
            last_frame=last_frame,
            images=images,
            audios=audios,
            seed=seed,
        )

        video_id = (
            create_result.get("video_id")
            or create_result.get("id")
            or create_result.get("task_id")
        )

        if not video_id:
            return {
                "success": False,
                "error": create_result,
            }

        elapsed = 0.0
        last_query_result = None

        while elapsed < max_wait_seconds:
            query_result = self.query_video(video_id)
            last_query_result = query_result

            status = query_result.get("status")

            if status == "completed":
                video_url = self._extract_video_url(query_result)

                if save_path and video_url:
                    self._save_video_from_url(video_url, save_path)

                return {
                    "success": True,
                    "video_id": video_id,
                    "video_url": video_url,
                    "save_path": save_path,
                    "raw": query_result,
                }

            if status == "failed":
                return {
                    "success": False,
                    "video_id": video_id,
                    "error": query_result,
                }

            time.sleep(poll_interval)
            elapsed += poll_interval

        return {
            "success": False,
            "video_id": video_id,
            "error": "Timed out waiting for video generation to complete.",
            "raw": last_query_result,
        }

    @staticmethod
    def _extract_video_url(query_result: dict) -> Optional[str]:
        """Try common response shapes to find the resulting video URL."""

        if not isinstance(query_result, dict):
            return None

        if query_result.get("video_url"):
            return query_result["video_url"]

        if query_result.get("url"):
            return query_result["url"]

        data = query_result.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                return first.get("url") or first.get("video_url")

        if isinstance(data, dict):
            return data.get("url") or data.get("video_url")

        return None
