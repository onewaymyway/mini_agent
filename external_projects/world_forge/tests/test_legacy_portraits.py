from world_forge import engine, proposals, repo
from world_forge.providers.image import StubImageProvider


def _char(world, name):
    return proposals.submit(world, "create_character", payload={"name": name, "role": "次要"})["result"]["id"]


def _set(world, cid, **fields):
    chars = repo.load_characters(world)
    ch = repo.find_by_id(chars, cid)
    for k, v in fields.items():
        if v is None:
            ch.pop(k, None)
        else:
            ch[k] = v
    repo.save_characters(world, chars)


def test_portrait_recorded_only_in_attrs_is_resolved(world, llm):
    cid = _char(world, "旧角色")
    # 旧版本：图片存在别处，只在角色属性中记录了位置
    other = repo.world_dir(world) / "cast" / "old_portraits" / "x.png"
    other.parent.mkdir(parents=True, exist_ok=True)
    StubImageProvider().generate("p", other)
    rel = other.relative_to(repo.world_dir(world))
    _set(world, cid, attrs={"portrait": str(rel)})
    ch = repo.find_by_id(repo.load_characters(world), cid)
    assert repo.resolve_portrait(world, ch) == other
    assert cid not in [c["id"] for c in engine.portrait_targets(world)]   # 已有图，不会重复生成


def test_empty_portrait_file_counts_as_missing(world):
    cid = _char(world, "空图")
    p = repo.portrait_path(world, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    ch = repo.find_by_id(repo.load_characters(world), cid)
    assert repo.resolve_portrait(world, ch) is None
    assert cid in [c["id"] for c in engine.portrait_targets(world)]


def test_character_without_status_field_is_still_a_target(world):
    cid = _char(world, "无状态")
    _set(world, cid, status=None)
    assert cid in [c["id"] for c in engine.portrait_targets(world)]


def test_dead_character_is_skipped_by_batch_but_card_still_offers_button(world, llm):
    cid = _char(world, "死者")
    _set(world, cid, status="dead")
    assert cid not in [c["id"] for c in engine.portrait_targets(world)]


def test_cards_show_legacy_portrait_and_buttons_for_every_status(tmp_path, monkeypatch, world, llm):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path

    alive = _char(world, "活着的")
    dead = _char(world, "死去的")
    nostatus = _char(world, "没有状态的")
    _set(world, dead, status="dead")
    _set(world, nostatus, status=None)
    # 三个角色各有一张旧图：一个在固定路径，一个只在属性里，一个在固定路径（已死亡）
    StubImageProvider().generate("a", repo.portrait_path(world, alive))
    other = repo.world_dir(world) / "cast" / "legacy.png"
    other.parent.mkdir(parents=True, exist_ok=True)
    StubImageProvider().generate("b", other)
    _set(world, nostatus, attrs={"portrait": "cast/legacy.png"})
    StubImageProvider().generate("c", repo.portrait_path(world, dead))

    root = Path(__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "app.py"), default_timeout=120).run()
    assert not at.exception, at.exception

    def _find(node, typ):
        out = [node] if getattr(node, "type", None) == typ else []
        kids = getattr(node, "children", {})
        for k in (kids.values() if isinstance(kids, dict) else []):
            out += _find(k, typ)
        return out

    assert len(_find(at._tree, "image")) >= 3, "三个角色的旧人设图都应显示"
    labels = [b.label for b in at.button]
    assert labels.count("重新生成人设图") >= 3, "每个角色都应有重新生成按钮（包括已死亡的）"
