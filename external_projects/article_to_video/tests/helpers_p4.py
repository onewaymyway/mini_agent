"""P4 测试辅助：已确认口播稿与配音（时间轴）的项目；失败用的生图 provider。"""
from article_to_video.audio import synth as sy
from article_to_video.planning.runner import load_spine
from article_to_video.providers.image import ImageResult
from article_to_video.providers.tts import SilenceTTS
from article_to_video.script import writer as w
from helpers_p2 import ScriptLLM, good_script, ready_project


def scripted_project(root):
    """大纲已确认，所有章的口播稿已确认。返回 (项目目录, spine)。"""
    pdir = ready_project(root)
    spine = load_spine(pdir)
    for p in spine["parts"]:
        w.write_part(pdir, p["id"], ScriptLLM(good_script))
        w.confirm_script(pdir, p["id"])
    return pdir, load_spine(pdir)


def voiced_project(root):
    """在 scripted_project 基础上完成静音配音（生成各章 timeline.json）。"""
    pdir, spine = scripted_project(root)
    for p in spine["parts"]:
        sy.synthesize_part(pdir, p["id"], SilenceTTS())
    return pdir, spine


class FailingImageProvider:
    name = "failing"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt, save_path, *, ratio=None, size="1024x1024", reference_images=None):
        self.calls += 1
        return ImageResult(False, error="模拟生图失败", non_retryable=False)
