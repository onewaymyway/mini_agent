from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, List

import requests


class AgnesVideoClient:
    BASE_URL = "https://apihub.agnes-ai.com/v1"
    QUERY_URL = "https://apihub.agnes-ai.com/agnesapi"
    MODEL = "agnes-video-2.5-flash"

    def __init__(
        self,
        api_key: str,
        timeout: int = 1800,
        max_retries: int = 3,
        verify_ssl: bool = False,
    ):
        self.api_key = api_key
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
    def _post(self, url: str, payload: dict) -> dict:
        last_err = None

        for i in range(self.max_retries):
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

    def _get(self, url: str, params: dict) -> dict:
        last_err = None

        for i in range(self.max_retries):
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
