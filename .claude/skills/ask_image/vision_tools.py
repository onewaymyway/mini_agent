from __future__ import annotations

import os
import json
import time
import base64
import mimetypes
from pathlib import Path
from typing import Generator

import requests


class NvidiaVisionClient:
    """
    NVIDIA Multimodal Client

    支持：
        - 单图
        - 多图
        - 流式输出
        - 自动重试
        - 图片问答
        - OCR
        - 图片分析
    """

    ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"

    def __init__(
        self,
        model: str = "qwen/qwen3.5-122b-a10b",
        timeout: int = 300,
        max_retries: int = 10,
        retry_delay: float = 5.0,
    ):
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        self.api_key = os.getenv("NVIDIA_API_KEY")

        if not self.api_key:
            raise ValueError(
                "Environment variable NVIDIA_API_KEY not found"
            )

    @staticmethod
    def _image_to_data_url(image_path: str) -> str:
        """
        本地图片转 Data URL
        """

        path = Path(image_path)

        if not path.exists():
            raise FileNotFoundError(image_path)

        mime_type, _ = mimetypes.guess_type(path)

        if mime_type is None:
            mime_type = "image/jpeg"

        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        return f"data:{mime_type};base64,{image_b64}"

    def _build_messages(
        self,
        image_paths: list[str],
        prompt: str,
    ) -> list:
        content = []

        for image_path in image_paths:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._image_to_data_url(image_path)
                    },
                }
            )

        content.append(
            {
                "type": "text",
                "text": prompt,
            }
        )

        return [
            {
                "role": "user",
                "content": content,
            }
        ]

    def _build_payload(
        self,
        image_paths: list[str],
        prompt: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict:
        return {
            "model": self.model,
            "messages": self._build_messages(
                image_paths=image_paths,
                prompt=prompt,
            ),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    def _stream_request(
        self,
        payload: dict,
    ) -> Generator[str, None, None]:

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        last_error = None

        for retry in range(self.max_retries):

            try:

                with requests.post(
                    self.ENDPOINT,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=self.timeout,
                    verify=False
                ) as response:

                    response.raise_for_status()

                    for line in response.iter_lines():

                        if not line:
                            continue

                        line = line.decode("utf-8")

                        if not line.startswith("data: "):
                            continue

                        data = line[6:]

                        if data == "[DONE]":
                            return

                        try:
                            chunk = json.loads(data)

                            delta = (
                                chunk["choices"][0]
                                .get("delta", {})
                                .get("content", "")
                            )

                            if delta:
                                yield delta

                        except Exception:
                            continue

                    return

            except Exception as e:
                import traceback
                traceback.print_exc()

                last_error = e

                if retry < self.max_retries - 1:

                    wait_time = self.retry_delay * (2**retry)

                    print(
                        f"\nRetry {retry + 1}/{self.max_retries}"
                        f" after {wait_time:.1f}s..."
                    )

                    time.sleep(wait_time)

                else:
                    raise last_error

    def stream_chat(
        self,
        image_paths: str | list[str],
        prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """
        流式输出

        Example:

        for chunk in client.stream_chat(...):
            print(chunk, end="")
        """

        if isinstance(image_paths, str):
            image_paths = [image_paths]

        payload = self._build_payload(
            image_paths=image_paths,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        yield from self._stream_request(payload)

    def chat(
        self,
        image_paths: str | list[str],
        prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """
        获取完整结果

        Example:

        result = client.chat(...)
        """

        result = []

        for chunk in self.stream_chat(
            image_paths=image_paths,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            result.append(chunk)

        return "".join(result)

    def chat_stream_print(
        self,
        image_paths: str | list[str],
        prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        print_stream: bool = True,
    ) -> str:
        """
        一边流式打印
        一边返回完整结果

        Example:

        result = client.chat_stream_print(
            "test.jpg",
            "详细描述图片"
        )
        """

        for retry in range(self.max_retries):

            result = []

            for chunk in self.stream_chat(
                image_paths=image_paths,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            ):

                result.append(chunk)

                if print_stream:
                    print(chunk, end="", flush=True)
            result_str="".join(result)
            if result_str:
                return result_str
        return ""