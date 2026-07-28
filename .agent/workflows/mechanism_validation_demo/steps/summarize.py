"""mechanism_validation_demo 的 summarize_report 步骤脚本。

演示 python_step 的 ctx.input_json() 便捷方法：
- 读 generate_words（script 类型，声明了 result_file）——优先读结构化
  文件，不用解析 stdout 文本。
- 读 enrich_each_word（foreach 类型，没有 result_file）——退回解析
  output 文本；foreach 的输出本身就是一段合法 JSON 数组文本，
  所以这里 json.loads 能直接解析成功。
"""

from typing import Any


def run(ctx) -> dict:
    words_data = ctx.input_json("generate_words", {})
    words = words_data.get("words", []) if isinstance(words_data, dict) else []

    enrich_raw = ctx.input_json("enrich_each_word", [])
    enriched_outputs: list[Any] = []
    errors: list[Any] = []
    if isinstance(enrich_raw, list):
        for entry in enrich_raw:
            if not isinstance(entry, dict):
                continue
            if "error" in entry:
                errors.append(entry)
            else:
                enriched_outputs.append(entry.get("output"))

    return {
        "total_words": len(words),
        "enriched_count": len(enriched_outputs),
        "error_count": len(errors),
        "enriched_raw_outputs": enriched_outputs,
        "errors": errors,
    }
