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

    # 检查 NVIDIA_API_KEY
    if not os.getenv("NVIDIA_API_KEY"):
        print("错误：缺少 NVIDIA_API_KEY 环境变量")
        print("请运行：export NVIDIA_API_KEY=your_api_key")
        sys.exit(1)


    try:
        from vision_tools import NvidiaVisionClient
    except ImportError:
        print("错误：找不到 vision_tools.py，请确保它在当前工作目录")
        sys.exit(1)

    # 创建客户端并发起请求
    client = NvidiaVisionClient()

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
