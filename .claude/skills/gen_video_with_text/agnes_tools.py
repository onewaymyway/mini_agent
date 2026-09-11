from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Optional, List

import requests

from agnes_key_pool import AgnesKeyPool, is_rate_limit_error, is_non_retryable_error


class AgnesVideoClient:
    BASE_URL = "https://apihub.agnes-ai.com/v1"
    QUERY_URL = "https://apihub.agnes-ai.com/agnesapi"
    MODEL = "agnes-video-2.5-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        key_pool: Optional[AgnesKeyPool] = None,
        # [TIMEOUT-FIX] 原来默认 1800s（30 分钟），但这个 timeout 实际只用在
        # create_video() 这个"创建任务"请求上——该接口本身应该很快返回，真正
        # 耗时的生成过程在 generate_video() 的轮询阶段异步完成，不受这个值影响。
        # 30 分钟的 read timeout 意味着一旦网络/服务端卡住不响应，配合
        # max_retries=3 最坏要等 90 分钟且中途无输出，才会报错。改成 120s，
        # 配合上面新加的重试日志，能更快暴露问题；如需要更长可显式传入。
        timeout: int = 120,
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
                # [TIMEOUT-FIX] 用 (connect_timeout, read_timeout) 元组代替单一数字，
                # 避免"连不上"这种应该很快失败的情况也要等满 self.timeout（默认 30 分钟）。
                # connect 阶段给 15s 足够；read 阶段仍保留 self.timeout，
                # 因为创建任务接口本身应该很快返回，真正耗时的生成过程是异步轮询的。
                resp = self.session.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=(15, self.timeout),
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

                    if is_non_retryable_error(resp.status_code, resp.text):
                        # [FAST-FAIL] 参数错误/鉴权失败/内容被拒绝等：换 key、
                        # 重试都不会改变结果，立即停止重试并原样返回，交给
                        # 调用方打印结构化错误、终止脚本，让 Agent 去修配置。
                        print(f"    [POST 不可重试错误] status_code={resp.status_code}，"
                              f"判定为参数/鉴权/内容类错误，立即停止重试: {resp.text[:200]}")
                        return {"success": False, "error": last_err, "non_retryable": True}

                    attempt += 1
                    # [LOG-FIX] 之前这里重试/异常完全没有输出，外部看起来像卡死。
                    print(f"    [POST 失败] status_code={resp.status_code}，"
                          f"第 {attempt}/{self.max_retries} 次重试，"
                          f"{1.5 * attempt:.1f}s 后重试: {resp.text[:200]}")
                    time.sleep(1.5 * attempt)
                    continue

                if self.key_pool:
                    self.key_pool.report_success(self.api_key)

                return resp.json()

            except Exception as e:
                last_err = str(e)
                attempt += 1
                # [LOG-FIX] 网络异常（超时/连接失败等）原来是静默重试，加上日志方便判断是否是超时问题。
                print(f"    [POST 异常] {e}，第 {attempt}/{self.max_retries} 次重试，"
                      f"{1.5 * attempt:.1f}s 后重试")
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
                # [TIMEOUT-FIX] 同样拆分 connect/read timeout，connect 阶段 10s 即可判定失败。
                resp = self.session.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=(10, 60),
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

                    if is_non_retryable_error(resp.status_code, resp.text):
                        print(f"    [GET 不可重试错误] status_code={resp.status_code}，"
                              f"判定为参数/鉴权/内容类错误，立即停止重试: {resp.text[:200]}")
                        return {"success": False, "error": last_err, "non_retryable": True}

                    attempt += 1
                    # [LOG-FIX] 轮询请求失败原来完全静默，加日志便于判断是查询接口的问题
                    # 还是生成任务本身卡住了。
                    print(f"    [GET 失败] status_code={resp.status_code}，"
                          f"第 {attempt}/{self.max_retries} 次重试，"
                          f"{1.5 * attempt:.1f}s 后重试: {resp.text[:200]}")
                    time.sleep(1.5 * attempt)
                    continue

                if self.key_pool:
                    self.key_pool.report_success(self.api_key)

                return resp.json()

            except Exception as e:
                last_err = str(e)
                attempt += 1
                print(f"    [GET 异常] {e}，第 {attempt}/{self.max_retries} 次重试，"
                      f"{1.5 * attempt:.1f}s 后重试")
                time.sleep(1.5 * attempt)

        return {
            "success": False,
            "error": last_err,
        }

    # =========================
    # utils: save video
    # =========================
    def _save_video_from_url(self, url: str, save_path: str):
        """下载生成好的视频。[EXC-FIX] 原来只 try 一次，网络抖动/握手超时
        （比如 TimeoutError/ReadTimeoutError）会直接把异常抛给调用方，
        而 generate_video() 之前没有包住这一步，异常会一路冒到最外层
        main()，把整个批量生成脚本直接干掉——哪怕视频本身已经生成成功、
        前面几十个场景也都跑完了，也会因为最后下载这一步偶发超时而
        全部丢失、不再有任何汇总输出。这里改成和 _post/_get 一样的
        "重试 + 打日志"模式，下载失败不再是致命的。"""
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                # connect 10s / 单次 read 120s，避免下载环节连不上也要等满 120s。
                r = requests.get(url, timeout=(10, 120), verify=False, stream=True)
                r.raise_for_status()

                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                return
            except Exception as e:
                last_err = e
                print(f"    [下载失败] {e}，第 {attempt}/{self.max_retries} 次重试，"
                      f"{1.5 * attempt:.1f}s 后重试")
                # 清理下载到一半的残缺文件，避免留下看起来"存在但损坏"的产物。
                try:
                    p = Path(save_path)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
                if attempt < self.max_retries:
                    time.sleep(1.5 * attempt)

        raise RuntimeError(f"下载视频失败（重试 {self.max_retries} 次后仍失败）: {last_err}") from last_err

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
        poll_count = 0
        # [LOG-FIX] 轮询阶段原来完全没有输出，视频生成通常要几分钟，
        # 期间脚本其实在正常工作，但外部看起来像卡死。这里每隔约 30 秒
        # （poll_interval * HEARTBEAT_EVERY）打印一次心跳，带上当前状态。
        HEARTBEAT_EVERY = max(1, int(30 / poll_interval)) if poll_interval > 0 else 15

        while elapsed < max_wait_seconds:
            query_result = self.query_video(video_id)
            last_query_result = query_result
            poll_count += 1

            status = query_result.get("status")

            if poll_count % HEARTBEAT_EVERY == 0 and status not in ("completed", "failed"):
                print(f"    ...视频生成中，video_id={video_id}，"
                      f"已等待 {int(elapsed)}s/{max_wait_seconds}s，status={status}")

            if status == "completed":
                video_url = self._extract_video_url(query_result)

                if save_path and video_url:
                    # [EXC-FIX] 下载环节即使内部已经重试过仍可能最终失败
                    # （见 _save_video_from_url），这里再包一层，确保"生成
                    # 任务本身已经 completed，只是下载失败"这种情况也走
                    # 正常的 {"success": False, "error": ...} 返回路径，
                    # 而不是直接抛异常炸穿 generate_scene_videos.py 的
                    # 重试循环、干掉整个批量任务。
                    try:
                        self._save_video_from_url(video_url, save_path)
                    except Exception as e:
                        return {
                            "success": False,
                            "video_id": video_id,
                            "error": f"任务已生成完成，但下载视频失败: {e}",
                            "raw": query_result,
                        }

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
