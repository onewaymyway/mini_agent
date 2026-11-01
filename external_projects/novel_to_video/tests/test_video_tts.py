from pathlib import Path

from PIL import Image

from novel_to_video.media import ffmpeg as mf
from novel_to_video.providers.keypool import KeyPool
from novel_to_video.providers.tts import SilenceTTS, estimate_seconds, voice_for
from novel_to_video.providers.video import AgnesVideoProvider


class Resp:
    def __init__(self, status, body=None, text="", content=b""):
        self.status_code = status
        self._body = body
        self.text = text or ("" if body is None else str(body))
        self.content = content
        self.ok = 200 <= status < 300

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class VSession:
    """模拟：POST 创建任务（可先限流），GET 轮询（先 processing 后 completed），GET 下载。"""

    def __init__(self, create_responses, poll_states, video_bytes=b"MP4DATA"):
        self.create = list(create_responses)
        self.poll = list(poll_states)
        self.video_bytes = video_bytes
        self.auths = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.auths.append(headers["Authorization"])
        return self.create.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        if "agnesapi" in url:
            return Resp(200, self.poll.pop(0))
        return Resp(200, content=self.video_bytes)


def _ref(tmp_path):
    p = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (10, 20, 30)).save(p)
    return p


def test_agnes_video_create_poll_download(tmp_path):
    sess = VSession([Resp(200, {"video_id": "v1"})],
                    [{"status": "processing"}, {"status": "completed", "video_url": "https://x/v.mp4"}])
    prov = AgnesVideoProvider(KeyPool(["k1"]), session=sess, sleep=lambda s: None, poll_interval=0)
    out = tmp_path / "clip.mp4"
    res = prov.generate("prompt", out, seconds=6, reference_images=[_ref(tmp_path)])
    assert res.ok and out.read_bytes() == b"MP4DATA"


def test_agnes_video_rate_limit_switches_key(tmp_path):
    sess = VSession([Resp(429, text="rate limit"), Resp(200, {"video_id": "v2"})],
                    [{"status": "completed", "url": "https://x/v.mp4"}])
    prov = AgnesVideoProvider(KeyPool(["k1", "k2"]), session=sess, sleep=lambda s: None, poll_interval=0)
    res = prov.generate("p", tmp_path / "c.mp4", seconds=5, reference_images=[_ref(tmp_path)])
    assert res.ok and sess.auths == ["Bearer k1", "Bearer k2"]


def test_agnes_video_non_retryable(tmp_path):
    sess = VSession([Resp(400, text="bad seconds")], [])
    prov = AgnesVideoProvider(KeyPool(["k1"]), session=sess, sleep=lambda s: None)
    res = prov.generate("p", tmp_path / "d.mp4", seconds=5, reference_images=[_ref(tmp_path)])
    assert not res.ok and res.non_retryable


def test_agnes_video_failed_task(tmp_path):
    sess = VSession([Resp(200, {"video_id": "v3"})], [{"status": "failed", "error": "content"}])
    prov = AgnesVideoProvider(KeyPool(["k1"]), session=sess, sleep=lambda s: None, poll_interval=0)
    res = prov.generate("p", tmp_path / "e.mp4", seconds=5, reference_images=[_ref(tmp_path)])
    assert not res.ok and "失败" in res.error


def test_voice_mapping_and_estimate():
    assert voice_for("narration", None) == "zh-CN-YunjianNeural"
    assert voice_for("dialogue", "female") == "zh-CN-XiaoxiaoNeural"
    assert voice_for("dialogue", None) == "zh-CN-XiaoyiNeural"
    assert estimate_seconds("一二三四五六七八九十") > 2


def test_silence_tts_writes_wav(tmp_path):
    out = tmp_path / "a.wav"
    dur = SilenceTTS().synth("你好世界", out, "x")
    assert out.is_file() and dur > 0.5
    assert abs(mf.probe_duration(out) - dur) < 0.1


def test_still_clip_and_mux_duration(tmp_path):
    img = _ref(tmp_path)
    clip = mf.still_clip(img, tmp_path / "s.mp4", 4.5)
    assert abs(mf.probe_duration(clip) - 4.5) < 0.3
    wav = tmp_path / "a.wav"
    mf.write_silence_wav(wav, 2.0)
    muxed = mf.mux_clip(clip, wav, tmp_path / "m.mp4", 5.0)
    assert abs(mf.probe_duration(muxed) - 5.0) < 0.3


def test_concat(tmp_path):
    img = _ref(tmp_path)
    a = mf.still_clip(img, tmp_path / "a.mp4", 2.0)
    b = mf.still_clip(img, tmp_path / "b.mp4", 3.0)
    out = mf.concat([a, b], tmp_path / "all.mp4")
    assert abs(mf.probe_duration(out) - 5.0) < 0.4
