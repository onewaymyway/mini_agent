"""歌词对齐校正脚本（LLM版本）。

用途：ASR 识别结果文字通常有错字/漏字/背景音干扰导致的幻听，但时间戳大体可信；
用户提供的"标准歌词"文字准确但没有时间戳。本脚本通过 Agent/LLM 语义理解将两者对齐，
得到"文字准确 + 时间戳可信"的逐句歌词。

核心改进（相比纯脚本版 align_lyrics.py）：
- ASR识别错误多时（如中文歌曲），纯字符匹配会失败，LLM可通过语义理解完成对齐
- 支持同音字、近义字等ASR常见错误

用法（命令行）：
    python align_lyrics_llm.py asr_raw.json lyrics.txt --save-path lyrics_timed.json

或者由Agent直接调用：
    读取 asr_raw.json 和 lyrics.txt，通过LLM进行语义对齐，输出 lyrics_timed.json

依赖：openai 兼容 API（通过 AGNES_API_KEY 或 OPENAI_API_KEY）
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional
import os


def _build_prompt(asr_segments: List[dict], lyrics_lines: List[str]) -> str:
    """构建LLM对齐prompt。"""
    asr_block = "\n".join(
        f"[{s['start']:.1f}s-{s['end']:.1f}s] {s['text']}"
        for s in asr_segments
    )
    lyrics_block = "\n".join(
        f"{i+1}. {line}"
        for i, line in enumerate(lyrics_lines)
        if line.strip()
    )
    return f"""你是一个歌词对齐专家。请根据ASR识别的时间戳和文本，将标准歌词逐行与ASR片段对应起来。

【ASR识别结果】（时间戳+识别文本，可能有多字/少字/错字）：
{asr_block}

【标准歌词】（准确文本，但无时间戳）：
{lyrics_block}

【对齐要求】：
1. 每句标准歌词对应ASR中最相近的一段（可能包含多个ASR片段）
2. 忽略ASR中的错别字，通过语义/读音相似性进行匹配
3. 歌词中不含括号标注的结构标记（如[Verse]、[Chorus]等）也需参与对齐
4. 输出格式必须是严格的JSON数组，每个元素包含：
   - line_index: 标准歌词的行号（从1开始，仅含歌词的行）
   - lyric: 该行的标准歌词原文
   - start_time: 对应ASR片段的起始时间（秒）
   - end_time: 对应ASR片段的结束时间（秒）
5. 不要包含任何解释文字，只输出JSON数组

请输出JSON数组："""


def _extract_json(text: str) -> list:
    """从LLM响应中提取JSON数组。"""
    text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 块
    import re
    m = re.search(r'```json\s*([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试找到第一个 [ 到最后一个 ]
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法从LLM响应中提取JSON: {text[:500]}")


def align_lyrics_llm(
    asr_result: dict,
    lyrics_text: str,
    api_key: Optional[str] = None,
    model: str = "default",
) -> list:
    """使用LLM对齐歌词和ASR结果。"""
    # 过滤掉空行
    lyrics_lines = [l for l in lyrics_text.split('\n') if l.strip()]
    asr_segments = asr_result.get("segments", [])

    prompt = _build_prompt(asr_segments, lyrics_lines)

    # 获取客户端
    try:
        from openai import OpenClient
    except ImportError:
        raise RuntimeError("需要安装 openai 包：pip install openai")

    client_kwargs = {}
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("AGNES_API_BASE")
    if base_url:
        client_kwargs["base_url"] = base_url

    key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("AGNES_API_KEY")
    if not key:
        raise RuntimeError("未找到API密钥，请设置 OPENAI_API_KEY 或 AGNES_API_KEY")

    client = OpenClient(api_key=key, **client_kwargs)

    print("调用LLM进行歌词对齐...", flush=True)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=4096,
    )
    reply = response.choices[0].message.content
    if not reply:
        raise RuntimeError("LLM返回空响应")

    print(f"LLM响应长度: {len(reply)} chars", flush=True)
    alignments = _extract_json(reply)
    print(f"对齐结果: {len(alignments)} 行歌词已对齐", flush=True)
    return alignments


def _merge_to_timed_lines(
    alignments: list,
    lyrics_lines: List[str],
    asr_segments: List[dict],
    line_gap: float = 0.15,
) -> dict:
    """将LLM对齐结果转换为最终的歌词时间戳格式。"""
    # 构建行号到歌词文本的映射
    line_map = {}
    line_counter = 1
    for line in lyrics_lines:
        if line.strip():
            line_map[line_counter] = line.strip()
            line_counter += 1

    timed_lines = []
    last_end = 0.0
    for align in alignments:
        idx = align.get("line_index")
        lyric = align.get("lyric", line_map.get(idx, ""))
        start_time = align.get("start_time")
        end_time = align.get("end_time")

        if start_time is None or end_time is None:
            continue

        # 确保相邻行有最小间隔
        if start_time < last_end + line_gap:
            start_time = last_end + line_gap

        timed_lines.append({
            "start": round(start_time, 3),
            "end": round(end_time, 3),
            "text": lyric
        })
        last_end = end_time

    return {
        "duration": round(asr_segments[-1]["end"] if asr_segments else 0, 3),
        "lyrics": timed_lines
    }


def to_srt(result: dict) -> str:
    """将歌词结果转为SRT字幕格式。"""
    def fmt_time(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"

    lines = []
    for i, lyr in enumerate(result.get("lyrics", []), 1):
        lines.append(str(i))
        lines.append(f"{fmt_time(lyr['start'])} --> {fmt_time(lyr['end'])}")
        lines.append(lyr["text"])
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="LLM歌词对齐（支持ASR错别字）")
    parser.add_argument("asr_json", help="asr_transcribe.py 的输出JSON路径")
    parser.add_argument("lyrics_txt", help="标准歌词纯文本路径，逐行一句")
    parser.add_argument("--line-gap", type=float, default=0.15, help="相邻两句最小间隔秒数，默认0.15")
    parser.add_argument("--save-path", default=None, help="对齐结果保存路径（JSON）")
    parser.add_argument("--save-srt", default=None, help="同时输出.srt字幕文件路径")
    parser.add_argument("--model", default="default", help="LLM模型名")
    args = parser.parse_args()

    asr_path = Path(args.asr_json)
    lyrics_path = Path(args.lyrics_txt)
    if not asr_path.exists():
        print(f"找不到ASR结果文件: {asr_path}", file=sys.stderr)
        sys.exit(1)
    if not lyrics_path.exists():
        print(f"找不到歌词文件: {lyrics_path}", file=sys.stderr)
        sys.exit(1)

    asr_result = json.loads(asr_path.read_text(encoding="utf-8"))
    lyrics_text = lyrics_path.read_text(encoding="utf-8")

    try:
        alignments = align_lyrics_llm(asr_result, lyrics_text, model=args.model)
    except Exception as exc:
        print(f"对齐失败: {exc}", file=sys.stderr)
        sys.exit(1)

    result = _merge_to_timed_lines(alignments, lyrics_text.split('\n'), asr_result.get("segments", []), line_gap=args.line_gap)

    if args.save_path:
        save_path = Path(args.save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存对齐结果到: {save_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.save_srt:
        srt_path = Path(args.save_srt)
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(to_srt(result), encoding="utf-8")
        print(f"已保存字幕文件到: {srt_path}")


if __name__ == "__main__":
    main()
