"""动态漫模式 S1：图像接口的参考图参数（数量上限、缩放编码、缺失即报错、无参考图时请求体不变）。"""
import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from novel_to_video.providers.image import (MAX_REFERENCE_IMAGES, REFERENCE_MAX_EDGE, AgnesImageProvider,
                                           ReferenceError, StubImageProvider, encode_reference,
                                           prepare_references, select_references)
from novel_to_video.providers.keypool import KeyPool


class Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        return self._body


def _png_b64():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"payload": json, "auth": headers["Authorization"]})
        return self.responses.pop(0)


def ok():
    return Resp(200, {"data": [{"b64_json": _png_b64()}]})


def _img(path: Path, size=(64, 96), mode="RGB", color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path, format="PNG")
    return path


def _pool():
    return KeyPool(["k1", "k2"])


def _decode(uri: str) -> Image.Image:
    assert uri.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))


# ---------- 请求体：无参考图时与改动前一致

def test_no_reference_payload_is_unchanged(tmp_path):
    sess = FakeSession([ok()])
    prov = AgnesImageProvider(_pool(), session=sess, sleep=lambda s: None)
    r = prov.generate("一个角色", tmp_path / "a.png", ratio="9:16", size="1K")
    assert r.ok and r.ref_count == 0 and r.dropped_refs == []
    assert sess.calls[0]["payload"] == {
        "model": "agnes-image-2.5-flash",
        "prompt": "一个角色",
        "size": "1K",
        "extra_body": {"response_format": "b64_json"},
        "ratio": "9:16",
    }


def test_empty_reference_list_behaves_like_no_reference(tmp_path):
    sess = FakeSession([ok()])
    prov = AgnesImageProvider(_pool(), session=sess, sleep=lambda s: None)
    prov.generate("x", tmp_path / "a.png", reference_images=[])
    assert "image" not in sess.calls[0]["payload"]["extra_body"]


# ---------- 参考图：数量、顺序、编码

def test_references_sent_as_data_uris_in_order(tmp_path):
    refs = [_img(tmp_path / f"r{i}.png", color=(i * 40, 0, 0)) for i in range(2)]
    sess = FakeSession([ok()])
    prov = AgnesImageProvider(_pool(), session=sess, sleep=lambda s: None)
    r = prov.generate("合成", tmp_path / "o.png", ratio="9:16", size="1K", reference_images=refs)
    payload = sess.calls[0]["payload"]
    uris = payload["extra_body"]["image"]
    assert len(uris) == 2 and all(u.startswith("data:image/png;base64,") for u in uris)
    assert payload["extra_body"]["response_format"] == "b64_json"   # 不丢失原有字段
    assert payload["ratio"] == "9:16" and payload["size"] == "1K"
    assert r.ref_count == 2 and r.dropped_refs == []
    # 顺序：第一张参考图的红色分量应为 0（color=(0,0,0)）
    assert _decode(uris[0]).getpixel((0, 0))[0] == 0
    assert _decode(uris[1]).getpixel((0, 0))[0] == 40


def test_more_than_three_references_are_truncated_and_reported(tmp_path):
    refs = [_img(tmp_path / f"r{i}.png") for i in range(5)]
    kept, dropped = select_references(refs)
    assert kept == refs[:MAX_REFERENCE_IMAGES] and dropped == refs[3:]
    sess = FakeSession([ok()])
    prov = AgnesImageProvider(_pool(), session=sess, sleep=lambda s: None)
    r = prov.generate("x", tmp_path / "o.png", reference_images=refs)
    assert len(sess.calls[0]["payload"]["extra_body"]["image"]) == 3
    assert r.ref_count == 3
    assert r.dropped_refs == [str(p) for p in refs[3:]]


def test_limit_is_three():
    assert MAX_REFERENCE_IMAGES == 3


def test_large_reference_is_downscaled_keeping_aspect(tmp_path):
    big = _img(tmp_path / "big.png", size=(2000, 3000))
    uri = encode_reference(big)
    im = _decode(uri)
    assert max(im.size) == REFERENCE_MAX_EDGE
    assert im.size == (round(2000 * REFERENCE_MAX_EDGE / 3000), REFERENCE_MAX_EDGE)


def test_small_reference_is_not_upscaled(tmp_path):
    small = _img(tmp_path / "s.png", size=(64, 96))
    assert _decode(encode_reference(small)).size == (64, 96)


def test_rgba_and_jpeg_inputs_are_converted_to_png(tmp_path):
    rgba = tmp_path / "t.png"
    Image.new("RGBA", (32, 32), (10, 20, 30, 128)).save(rgba)
    jpg = tmp_path / "t.jpg"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(jpg, format="JPEG")
    assert _decode(encode_reference(rgba)).mode == "RGB"
    assert _decode(encode_reference(jpg)).size == (32, 32)


def test_prepare_references_encodes_only_kept_ones(tmp_path):
    refs = [_img(tmp_path / f"r{i}.png") for i in range(4)]
    uris, dropped = prepare_references(refs)
    assert len(uris) == 3 and dropped == [refs[3]]


# ---------- 错误：缺失或损坏的参考图在发请求前报错

def test_missing_reference_fails_before_any_request(tmp_path):
    sess = FakeSession([ok()])
    prov = AgnesImageProvider(_pool(), session=sess, sleep=lambda s: None)
    r = prov.generate("x", tmp_path / "o.png", reference_images=[tmp_path / "nope.png"])
    assert not r.ok and r.non_retryable
    assert "参考图不存在" in r.error
    assert sess.calls == []        # 没有消耗 key 与请求次数


def test_corrupt_reference_fails_before_any_request(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    sess = FakeSession([ok()])
    prov = AgnesImageProvider(_pool(), session=sess, sleep=lambda s: None)
    r = prov.generate("x", tmp_path / "o.png", reference_images=[bad])
    assert not r.ok and r.non_retryable and "无法读取" in r.error
    assert sess.calls == []


def test_encode_reference_raises_reference_error(tmp_path):
    with pytest.raises(ReferenceError):
        encode_reference(tmp_path / "missing.png")


# ---------- 重试沿用同一份编码后的参考图

def test_retry_reuses_identical_payload(tmp_path):
    refs = [_img(tmp_path / "r.png")]
    sess = FakeSession([Resp(500, text="boom"), ok()])
    prov = AgnesImageProvider(_pool(), session=sess, max_retries=3, sleep=lambda s: None)
    r = prov.generate("x", tmp_path / "o.png", reference_images=refs)
    assert r.ok and len(sess.calls) == 2
    assert sess.calls[0]["payload"] == sess.calls[1]["payload"]


# ---------- 桩与真实 provider 行为一致

def test_stub_records_references_and_truncates(tmp_path):
    refs = [_img(tmp_path / f"r{i}.png") for i in range(4)]
    stub = StubImageProvider()
    r = stub.generate("场景", tmp_path / "o.png", reference_images=refs)
    assert r.ok and r.ref_count == 3 and r.dropped_refs == [str(refs[3])]
    assert stub.reference_log == [{"prompt": "场景", "refs": ["r0.png", "r1.png", "r2.png"]}]


def test_stub_without_references_unchanged(tmp_path):
    stub = StubImageProvider()
    r = stub.generate("场景", tmp_path / "o.png")
    assert r.ok and r.ref_count == 0 and stub.reference_log == [{"prompt": "场景", "refs": []}]


def test_stub_missing_reference_is_error(tmp_path):
    r = StubImageProvider().generate("x", tmp_path / "o.png", reference_images=[tmp_path / "no.png"])
    assert not r.ok and r.non_retryable


def test_skill_image_provider_accepts_references(tmp_path):
    """回归：SkillImageProvider.generate() 曾不接受 reference_images，导致 C-8a 场景图 TypeError。
    有参考图走 skill 的 edit_image（data URI 列表，最多 3 张）；无参考图仍走 generate_image；无效参考图不发请求。"""
    from novel_to_video.providers.skill_providers import SkillImageProvider

    calls = []

    class FakeClient:
        model = "m"

        def generate_image(self, **kw):
            calls.append(("gen", kw))
            Path(kw["save_path"]).write_bytes(b"x")
            return {"success": True}

        def edit_image(self, **kw):
            calls.append(("edit", kw))
            Path(kw["save_path"]).write_bytes(b"x")
            return {"success": True}

    prov = SkillImageProvider.__new__(SkillImageProvider)
    prov.client = FakeClient()
    prov.use_system_proxy = False
    refs = [_img(tmp_path / f"r{i}.png") for i in range(4)]

    r = prov.generate("p", tmp_path / "o1.png", ratio="16:9", size="1K", reference_images=refs)
    assert r.ok and r.ref_count == 3 and len(r.dropped_refs) == 1
    kind, kw = calls[-1]
    assert kind == "edit" and len(kw["image_url"]) == 3 and kw["image_url"][0].startswith("data:image")

    r = prov.generate("p", tmp_path / "o2.png", ratio="16:9", size="1K")
    assert r.ok and r.ref_count == 0 and calls[-1][0] == "gen"

    n = len(calls)
    r = prov.generate("p", tmp_path / "o3.png", reference_images=[tmp_path / "missing.png"])
    assert not r.ok and r.non_retryable and len(calls) == n
