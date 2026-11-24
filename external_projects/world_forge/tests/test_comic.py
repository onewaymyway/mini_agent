import json
import re

from world_forge import engine, generate, proposals, repo, store


def _story(world, llm, n_char=True):
    if n_char:
        proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要",
                                                            "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def test_storyboard_panels_get_shot_span_and_bubble_defaults():
    d = {"pages": [{"no": 1, "panels": [
        {"visual_en": "a", "span": 2, "dialogue": [{"speaker": "甲", "text": "你好"}]},
        {"visual_en": "b", "shot": "不认识的", "span": "3", "dialogue": []},
    ]}]}
    assert generate._validate_storyboard(d) == []
    p1, p2 = d["pages"][0]["panels"]
    assert p1["span"] == 2 and p1["shot"] == "中景"
    assert p1["dialogue"][0]["position"] in generate.BUBBLE_POSITIONS
    assert p2["span"] == 2 and p2["shot"] == "中景"


def test_storyboard_rejects_missing_visual():
    d = {"pages": [{"no": 1, "panels": [{"narration": "只有旁白"}]}]}
    assert generate._validate_storyboard(d)


def test_storyboard_creates_appearance_anchor_for_characters(world, llm):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid)
    ch = next(c for c in repo.load_characters(world) if c["name"] == "守灯人")
    assert ch["attrs"].get("visual_en"), "出场角色应获得固定外观描述"


def test_panels_carry_style_anchor_and_aspect_ratio(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid)
    board = store.read_json(repo.story_dir(world, sid) / "storyboard.json")
    board["pages"][0]["panels"][0]["span"] = 2
    board["pages"][0]["panels"][0]["characters"] = ["守灯人"]
    store.write_json(repo.story_dir(world, sid) / "storyboard.json", board)
    engine.render_comic(img, world, sid)
    first = img.calls[0]
    assert "comic book illustration" in first["prompt"]
    assert first["ratio"] == "16:9"
    assert "守灯人:" in first["prompt"], "出场角色的外观锚点必须写入提示词"


def test_each_later_panel_references_previous_panel(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid)
    engine.render_comic(img, world, sid)
    assert len(img.calls) >= 2
    assert img.calls[0]["refs"] == []                      # 第一格没有前文
    assert img.calls[1]["refs"] and img.calls[1]["refs"][0].endswith(".png")
    board = store.read_json(repo.story_dir(world, sid) / "storyboard.json")
    first_panel = board["pages"][0]["panels"][0]["image"]
    assert img.calls[1]["refs"][0] == first_panel.split("/")[-1]   # 第二格以第一格为参考


def test_comic_html_is_a_page_grid_with_bubbles_and_embedded_images(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid)
    engine.render_comic(img, world, sid)
    html_text = (repo.story_dir(world, sid) / "comic.html").read_text(encoding="utf-8")
    assert "class='page'" in html_text or 'class=\'page\'' in html_text
    assert "flex-wrap" in html_text and "class='grid'" in html_text
    assert "data:image/png;base64," in html_text          # 图片嵌入，文件可独立打开
    assert "panel wide" in html_text                       # 整行格
    assert re.search(r"class='bz (top|bottom) (l|r|c)'", html_text)   # 气泡区域叠在画面角落
    assert "class='bubble'" in html_text                   # 有对白就必须有气泡


def test_comic_html_escapes_dialogue(world, llm):
    board = {"pages": [{"no": 1, "panels": [
        {"visual_en": "x", "shot": "近景", "span": 1, "narration": "<script>x</script>",
         "dialogue": [{"speaker": "甲", "text": "</div><b>恶意</b>", "position": "左上"}], "characters": []}]}]}
    out = engine.render_comic_html(board, "测试", base_dir=repo.story_dir(world, "sty_none"))
    assert "<script>x</script>" not in out
    assert "</div><b>恶意</b>" not in out


def test_missing_panel_image_shows_placeholder(world):
    board = {"pages": [{"no": 1, "panels": [
        {"visual_en": "x", "shot": "中景", "span": 1, "narration": "", "dialogue": [], "characters": [],
         "image": None, "image_error": "额度不足"}]}]}
    out = engine.render_comic_html(board, "测试", base_dir=repo.story_dir(world, "sty_none"))
    assert "额度不足" in out and "class='missing'" in out
