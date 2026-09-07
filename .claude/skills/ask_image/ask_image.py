#!/usr/bin/env python3
"""
ask_image - 图片问答工具

用法:
    ask_image.py <图片路径> <prompt>

示例:
    ask_image.py "test.jpg" "详细描述这张图片"
    ask_image.py "screenshot.png" "截图里有什么错误信息？"
"""

# 修复 Windows 命令行编码问题（GBK -> UTF-8）
import sys
if sys.platform == "win32":
    import io
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import os
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("\n错误：缺少参数")
        print(f"用法：ask_image <图片路径> <prompt>")
        sys.exit(1)

    # 获取图片路径和 prompt
    image_path = sys.argv[1]
    prompt = " ".join(sys.argv[2:])

    # 验证图片路径
    if not Path(image_path).exists():
        print(f"错误：图片文件不存在：{image_path}")
        sys.exit(1)

    try:
        from vision_tools import AgnesVisionClient
        from agnes_key_pool import build_key_pool
    except ImportError:
        print("错误：找不到 vision_tools.py / agnes_key_pool.py，请确保它们在当前工作目录")
        sys.exit(1)

    # 检查 Agnes API key（AGNES_API_KEY / AGNES_API_KEYS / providers.json 任一即可）
    key_pool = build_key_pool(os.path.dirname(os.path.abspath(__file__)))
    if not key_pool:
        print("错误：未找到可用的 Agnes API key")
        print("请设置 AGNES_API_KEY（或 AGNES_API_KEYS，逗号分隔多个 key），或在 providers.json 中配置")
        sys.exit(1)

    # 创建客户端并发起请求
    client = AgnesVisionClient(key_pool=key_pool)

    print(f"\n[图片] {image_path}")
    print(f"[问题] {prompt}")
    print("\n" + "-" * 40 + "\n")

    result = client.chat_stream_print(
        image_paths=image_path,
        prompt=prompt,
    )

    print("\n" + "-" * 40 + "\n")
    print("\n" + "-" * 40 + "\n")
    print(f"[结果] {result}")
    print("\n完成")


if __name__ == "__main__":
    main()
