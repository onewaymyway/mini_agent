"""
llm/debug_logger.py — LLM 请求/响应调试日志

每次 LLM 调用写两条 JSONL 记录：request 和 response（或 error）。

日志格式（每行一条 JSON）：

  event=request:
  {
    "seq":      1,
    "ts":       "2025-01-01T12:00:00+00:00",
    "event":    "request",
    "provider": "nvidia",
    "model":    "stepfun-ai/step-3.5-flash",
    "request": {
      "raw": {                         # 调用方传入的原始输入
        "system":   "Be helpful",      # 未注入工具协议的 system
        "messages": [...],             # 原始对话历史
        "tools":    [...],             # 原始工具定义列表
        "stream":   true
      },
      "actual": {                      # 实际发给 API 的内容
        "system":   "Be helpful\\n\\n## Tool Call Format...",  # 注入工具协议后
        "messages": [...],
        "api_tools": [],               # 始终为空（工具通过 system 传递）
        "stream":   true
      }
    }
  }

  event=response:
  {
    "seq":         1,
    "ts":          "...",
    "event":       "response",
    "provider":    "nvidia",
    "model":       "...",
    "duration_ms": 1234,
    "response": {
      "raw": {                         # provider 返回的原始内容
        "text":      "<think>...</think>\\n<tool_use>...</tool_use>",
        "reasoning": "",               # 流式 reasoning_content（若有）
        "tool_calls": [],              # SDK 原生 tool_calls（通常为空）
        "stop_reason": "end_turn"
      },
      "processed": {                   # postprocess 后的最终结果
        "text":       "好的，我来创建文件。",   # 去掉 <tool_use> 和 <think> 后
        "reasoning":  "让我思考一下...",        # 从 <think> 提取
        "tool_calls": [{"name":"create_file","input":{...}}],
        "stop_reason": "tool_use"
      },
      "usage": {
        "input_tokens":  150,
        "output_tokens": 320,
        "total_tokens":  470
      }
    }
  }

  event=error:
  {
    "seq": 1, "event": "error", "duration_ms": 500,
    "error": "NVIDIA NIM timeout: ..."
  }
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── 全局序列号 ────────────────────────────────────────────────────────────────

_seq = 0

def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


# ── 配置 ──────────────────────────────────────────────────────────────────────

@dataclass
class DebugConfig:
    """调试日志配置。"""
    enabled: bool = False
    log_to_file: bool = True
    log_to_console: bool = False
    log_dir: Optional[Path] = None
    max_body_chars: int = 8000      # 单个字段最大字符数（防止日志过大）

    @classmethod
    def from_env(cls) -> "DebugConfig":
        return cls(
            enabled=os.environ.get("LLM_DEBUG", "").lower() in ("1", "true", "yes"),
            log_to_console=os.environ.get("LLM_DEBUG_CONSOLE", "").lower() in ("1", "true", "yes"),
            log_dir=Path(d) if (d := os.environ.get("LLM_DEBUG_LOG_DIR", "")) else None,
        )


# ── LLMDebugLogger ────────────────────────────────────────────────────────────

class LLMDebugLogger:
    """
    线程安全的 LLM 调试日志记录器。
    同时记录原始输入/输出和处理后的输入/输出，便于对比分析。

    调用方式（由 ProviderMixin 调用）：
        seq = logger.log_request(provider, model,
                                 raw_system, raw_messages, raw_tools,
                                 actual_system, actual_api_tools,
                                 stream)

        logger.log_response(seq, provider, model,
                            raw_response, processed_response,
                            duration_ms)

        logger.log_error(seq, provider, model, error, duration_ms)
    """

    def __init__(self, cfg: DebugConfig, project_root: Optional[Path] = None) -> None:
        self.cfg = cfg
        self._log_file: Optional[Path] = None
        self._py_logger: Optional[logging.Logger] = None

        if cfg.enabled and cfg.log_to_file:
            self._log_file = self._resolve_log_file(cfg.log_dir, project_root)
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            self._py_logger = self._build_file_logger(self._log_file)

        if cfg.enabled:
            self._print_info(f"LLM debug logging enabled → {self._log_file or '(console only)'}")

    # ── 公共 API ──────────────────────────────────────────────────────────────

    def log_request(
        self,
        provider: str,
        model: str,
        # 原始输入（调用方传入的，未经处理）
        raw_system: str,
        raw_messages: list[dict],
        raw_tools: list[Any],
        # 实际发给 API 的（经过 _prepare_tools 处理后）
        actual_system: str,
        actual_api_tools: list[Any],
        stream: bool,
    ) -> int:
        """
        记录请求的原始输入和实际发给 API 的内容。
        返回 seq（用于关联后续 response/error 记录）。
        """
        if not self.cfg.enabled:
            return 0

        seq = _next_seq()
        entry = {
            "seq": seq,
            "ts": _now_iso(),
            "event": "request",
            "provider": provider,
            "model": model,
            "request": {
                "raw": {
                    "system":   self._truncate(raw_system),
                    "messages": self._truncate_messages(raw_messages),
                    "tools":    self._truncate(json.dumps(raw_tools, ensure_ascii=False)),
                    "stream":   stream,
                },
                "actual": {
                    "system":    self._truncate(actual_system),
                    "messages":  self._truncate_messages(raw_messages),  # messages 不变
                    "api_tools": actual_api_tools,  # 始终为 []（工具通过 system 传递）
                    "stream":    stream,
                },
            },
        }
        self._emit(entry, label="REQUEST", color="cyan")
        return seq

    def log_response(
        self,
        seq: int,
        provider: str,
        model: str,
        raw_response: Any,          # postprocess 前的 LLMResponse
        processed_response: Any,    # postprocess 后的 LLMResponse
        duration_ms: int,
    ) -> None:
        """
        记录 provider 原始响应和 postprocess 后的结果，便于对比分析。
        """
        if not self.cfg.enabled:
            return

        def _fmt_response(resp: Any) -> dict:
            tc = [
                {"id": t.id, "name": t.name, "input": t.input}
                for t in getattr(resp, "tool_calls", [])
            ]
            usage = resp.usage
            return {
                "text":        self._truncate(resp.text or ""),
                "reasoning":   self._truncate(getattr(resp, "reasoning", "") or ""),
                "tool_calls":  tc,
                "stop_reason": resp.stop_reason,
                "usage": {
                    "input_tokens":  usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens":  usage.total_tokens,
                },
            }

        entry = {
            "seq":         seq,
            "ts":          _now_iso(),
            "event":       "response",
            "provider":    provider,
            "model":       model,
            "duration_ms": duration_ms,
            "response": {
                "raw":       _fmt_response(raw_response),
                "processed": _fmt_response(processed_response),
            },
        }
        self._emit(entry, label="RESPONSE", color="green")

    def log_error(
        self,
        seq: int,
        provider: str,
        model: str,
        error: Exception,
        duration_ms: int,
    ) -> None:
        """记录调用失败。"""
        if not self.cfg.enabled:
            return

        entry = {
            "seq":         seq,
            "ts":          _now_iso(),
            "event":       "error",
            "provider":    provider,
            "model":       model,
            "duration_ms": duration_ms,
            "error":       str(error),
        }
        self._emit(entry, label="ERROR", color="red")

    @property
    def log_file(self) -> Optional[Path]:
        return self._log_file

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _emit(self, entry: dict, label: str, color: str) -> None:
        line = json.dumps(entry, ensure_ascii=False)
        if self._py_logger:
            self._py_logger.info(line)
        if self.cfg.log_to_console:
            self._print_entry(entry, label, color)

    def _print_entry(self, entry: dict, label: str, color: str) -> None:
        try:
            from rich.console import Console
            from rich.syntax import Syntax
            console = Console(stderr=True)
            seq      = entry.get("seq", "?")
            provider = entry.get("provider", "")
            model    = entry.get("model", "")
            ts       = entry.get("ts", "")[:19]
            console.print(
                f"\n[{color}]── LLM {label} #{seq}[/{color}] "
                f"[dim]{provider}/{model} @ {ts}[/dim]"
            )
            # 高亮 raw vs processed 差异
            if label == "REQUEST":
                req = entry.get("request", {})
                self._print_section(console, "raw input", req.get("raw", {}), color)
                self._print_section(console, "actual to API", req.get("actual", {}), "yellow")
            elif label == "RESPONSE":
                resp = entry.get("response", {})
                self._print_section(console, "raw output", resp.get("raw", {}), "magenta")
                self._print_section(console, "processed output", resp.get("processed", {}), color)
            else:
                pretty = json.dumps(entry, ensure_ascii=False, indent=2)
                console.print(Syntax(pretty, "json", theme="ansi_dark",
                                     background_color="default", line_numbers=False))
        except Exception:
            print(f"\n[LLM {label} #{entry.get('seq')}] {json.dumps(entry)}", flush=True)

    @staticmethod
    def _print_section(console, title: str, data: dict, color: str) -> None:
        from rich.syntax import Syntax
        console.print(f"  [{color}]▸ {title}[/{color}]")
        pretty = json.dumps(data, ensure_ascii=False, indent=2)
        console.print(
            Syntax(pretty, "json", theme="ansi_dark",
                   background_color="default", line_numbers=False)
        )

    def _print_info(self, msg: str) -> None:
        try:
            from rich.console import Console
            Console(stderr=True).print(f"[dim]🔍 {msg}[/dim]")
        except Exception:
            print(f"[DEBUG] {msg}", flush=True)

    @staticmethod
    def _resolve_log_file(log_dir: Optional[Path], project_root: Optional[Path]) -> Path:
        base = (
            log_dir
            or (project_root / ".claude" / "logs" if project_root else None)
            or Path.cwd() / ".claude" / "logs"
        )
        date_str = datetime.now().strftime("%Y%m%d")
        return base / f"llm_debug_{date_str}.jsonl"

    @staticmethod
    def _build_file_logger(path: Path) -> logging.Logger:
        name = f"llm_debug_{id(path)}"
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(fh)
        return logger

    def _truncate(self, s: str) -> str:
        limit = self.cfg.max_body_chars
        if not s or len(s) <= limit:
            return s
        return s[:limit] + f"…[truncated {len(s) - limit} chars]"

    def _truncate_messages(self, messages: list[dict]) -> list[dict]:
        result = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                content = self._truncate(content)
            elif isinstance(content, list):
                content = [
                    {**b, "text": self._truncate(b.get("text", ""))}
                    if b.get("type") == "text" else b
                    for b in content
                ]
            result.append({**msg, "content": content})
        return result


# ── 模块级单例 ────────────────────────────────────────────────────────────────

_default_logger: Optional[LLMDebugLogger] = None


def get_debug_logger() -> LLMDebugLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = LLMDebugLogger(DebugConfig(enabled=False))
    return _default_logger


def init_debug_logger(cfg: DebugConfig, project_root: Optional[Path] = None) -> LLMDebugLogger:
    global _default_logger
    _default_logger = LLMDebugLogger(cfg, project_root)
    return _default_logger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
