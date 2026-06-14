from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Optional

import requests


class AgnesImageClient:
    ENDPOINT = "https://apihub.agnes-ai.com/v1/images/generations"
    MODEL = "agnes-image-2.1-flash"

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

        r = requests.get(url, timeout=60,verify=False)
        r.raise_for_status()

        with open(save_path, "wb") as f:
            f.write(r.content)

    def _save_image_from_base64(self, b64: str, save_path: str):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        image_bytes = base64.b64decode(b64)

        with open(save_path, "wb") as f:
            f.write(image_bytes)

    # =========================
    # low-level API
    # =========================
    def text_to_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        response_format: str = "url",
        save_path: Optional[str] = None,
    ) -> dict:

        payload = {
            "model": self.MODEL,
            "prompt": prompt,
            "size": size,
            "extra_body": {
                "response_format": response_format,
            },
        }

        result = self._post(payload)

        if not result.get("data"):
            return result
        
        print("result:",result)

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
        image: str,
        prompt: str,
        size: str = "1024x1024",
        response_format: str = "url",
        save_path: Optional[str] = None,
    ) -> dict:
        
        if "http" not in image:
            data_uri = self.file_to_data_uri(
                image
            )
            image=data_uri

        payload = {
            "model": self.MODEL,
            "prompt": prompt,
            "size": size,
            "image": [image],
            "extra_body": {
                "response_format": response_format,
            },
        }

        result = self._post(payload)

        if not result.get("data"):
            return result
        
        print("result:",result)

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
        save_path: Optional[str] = None,
    ) -> dict:

        result = self.text_to_image(
            prompt=prompt,
            size=size,
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
        image_url: str,
        prompt: str,
        size: str = "1024x1024",
        save_path: Optional[str] = None,
    ) -> dict:

        result = self.image_to_image(
            image=image_url,
            prompt=prompt,
            size=size,
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