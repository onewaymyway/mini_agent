"""
word_count_v1 — 统计一段文本的词数/字符数/不重复词数。

符合 hybrid_exec / python_step 通用的 run(ctx) 协议：
- ctx.params 是本次调用的输入（对应 TaskSpec.input_data）
- 返回值会被序列化为本次执行的 output（dict 会存 JSON，字符串原样存文本）
"""


def run(ctx):
    text = ctx.params.get("text", "")
    if not isinstance(text, str):
        raise ValueError(f"期望 ctx.params['text'] 是字符串，实际是 {type(text).__name__}")

    words = text.split()
    return {
        "word_count": len(words),
        "char_count": len(text),
        "unique_word_count": len(set(w.lower() for w in words)),
    }
