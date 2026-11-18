"""P7 验收（不在 pytest 默认集合中，耗时数分钟）：参考文章 G2 全自动跑完整个流水线，产出完整成片。

离线条件：文本模型用测试假模型（good_script，忠实于原文，无网络）；语音静音；生图占位；
画幅 720P 以缩短耗时。预算：最多 200 次生图、最长 60 分钟。
用法：PYTHONPATH=. python acceptance_p7.py [输出目录]
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from article_to_video.pipeline.run import run_pipeline
from article_to_video.project_store import read_json, write_json_atomic
from article_to_video.providers.image import StubImageProvider
from article_to_video.providers.tts import SilenceTTS
from helpers_p2 import ScriptLLM, good_script, make_project


def main(out_dir: str) -> int:
    root = Path(tempfile.mkdtemp())
    os.environ["ARTICLE_TO_VIDEO_HOME"] = str(root)
    pdir = make_project(root)
    pj = read_json(pdir / "project.json")
    pj.setdefault("settings", {}).setdefault("video", {})["resolution"] = "720P"
    write_json_atomic(pdir / "project.json", pj)
    st = run_pipeline(pdir, outline_engine="rules", until="export", gear="G2", max_images=200, max_minutes=60,
                      llm=ScriptLLM(good_script), tts=SilenceTTS(), image_provider=StubImageProvider())
    print(json.dumps({"state": st["state"], "steps": [s["stage"] + ":" + s["result"] for s in st["steps"]],
                      "outputs": st["outputs"], "budget": st["budget"], "error": st["error"]},
                     ensure_ascii=False, indent=1), flush=True)
    if st["state"] != "done":
        return 1
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in (pdir / "exports").iterdir():
        if f.is_file():
            shutil.copy(f, out / f.name)
    print("copied to", out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/p7_accept"))
