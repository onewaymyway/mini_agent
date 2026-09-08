#!/usr/bin/env python3
"""人声分离脚本 —— 对齐流程的第一步（强烈建议做，收益最大的改进）。

用 Demucs 把歌曲拆成人声轨（vocals.wav）和伴奏轨（no_vocals.wav）。之后
无论走"ASR + 模糊匹配"（align_lyrics_v2.py）还是"强制对齐"
（align_lyrics_forced.py），都应该把干净的人声轨作为输入，而不是带伴奏
的原始 mp3。原因：背景音乐/和声/混响是当前中文歌词对齐效果差的主要
干扰源之一，Whisper 的声学模型和强制对齐模型都是在近似纯人声/纯语音
数据上训练的，伴奏越重，识别和对齐的准确度掉得越厉害。

依赖：
    pip install demucs --break-system-packages
    （demucs 依赖 torch，首次安装体积较大，几百 MB）

模型权重：
    首次运行会自动下载 Demucs 预训练模型权重（默认 htdemucs），下载源是
    Meta 的模型托管服务，需要网络能访问对应域名。如果当前沙箱网络白名单
    没放开，会下载失败并报错——此时可以：
      1) 让环境管理员把下载所需域名加入网络白名单，或
      2) 在有网络的机器上先跑一次（模型会缓存在
         ~/.cache/torch/hub/checkpoints/ 下），把这个缓存目录整体拷贝到
         当前沙箱环境的同一路径，之后无需联网即可复用。

用法：
    python separate_vocals.py <input.mp3> --output-dir <output_dir>

输出：
    <output_dir>/vocals/htdemucs/<歌曲文件名(不含后缀)>/vocals.wav     纯人声
    <output_dir>/vocals/htdemucs/<歌曲文件名(不含后缀)>/no_vocals.wav  纯伴奏
"""
import argparse
import subprocess
import sys
from pathlib import Path


def separate(input_audio: Path, output_dir: Path, model: str = "htdemucs",
             device: str = "cpu") -> dict:
    if not input_audio.exists():
        raise FileNotFoundError(f"输入音频不存在: {input_audio}")
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems", "vocals",
        "-n", model,
        "-d", device,
        "-o", str(output_dir),
        str(input_audio),
    ]
    print(f"[separate_vocals] 运行: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(
            "Demucs 人声分离失败。常见原因排查顺序：\n"
            "  1) 未安装 demucs -> pip install demucs --break-system-packages\n"
            "  2) 模型权重下载失败（需要网络）-> 检查网络白名单，或在有网络的机器上"
            "预下载后拷贝 ~/.cache/torch/hub/checkpoints/ 目录过来\n"
            "  3) 输入音频文件损坏、格式不支持或路径写错\n"
            f"完整错误输出见上方 stdout/stderr。"
        )

    stem = input_audio.stem
    vocals_path = output_dir / model / stem / "vocals.wav"
    accomp_path = output_dir / model / stem / "no_vocals.wav"
    if not vocals_path.exists():
        raise RuntimeError(
            f"Demucs 进程正常退出但未找到预期输出文件：{vocals_path}\n"
            f"请检查 {output_dir / model} 目录下实际生成了什么文件（不同版本的 "
            f"demucs 输出子目录结构可能略有差异）。"
        )
    return {"vocals": str(vocals_path), "accompaniment": str(accomp_path)}


def main():
    parser = argparse.ArgumentParser(description="用 Demucs 分离人声与伴奏，供后续对齐/ASR 使用")
    parser.add_argument("input_audio", help="输入歌曲文件路径（mp3/wav 等）")
    parser.add_argument("--output-dir", required=True, help="输出根目录，会在其下创建 vocals/ 子目录")
    parser.add_argument("--model", default="htdemucs",
                         help="Demucs 模型名，默认 htdemucs（质量最好，速度较慢，CPU 上一首歌可能要几分钟）；"
                              "追求速度可尝试 htdemucs_ft 或 mdx_extra_q")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"], help="推理设备，默认 cpu")
    args = parser.parse_args()

    result = separate(Path(args.input_audio), Path(args.output_dir) / "vocals",
                       args.model, args.device)
    print(f"vocals: {result['vocals']}")
    print(f"accompaniment: {result['accompaniment']}")


if __name__ == "__main__":
    main()
