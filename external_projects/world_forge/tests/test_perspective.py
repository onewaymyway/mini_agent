from world_forge import perspective, proposals, repo


def _ent(world, name, kind="地理"):
    return proposals.submit(world, "create_entity", payload={"kind": kind, "name": name, "summary": "x"})["result"]["id"]


def _char(world, name):
    return proposals.submit(world, "create_character", payload={"name": name, "role": "次要"})["result"]["id"]


def test_each_subject_gets_only_its_own_events(world):
    a = _char(world, "甲")
    b = _char(world, "乙")
    proposals.submit(world, "add_event", payload={"title": "只有甲", "involves": [a]})
    proposals.submit(world, "add_event", payload={"title": "甲和乙", "involves": [a, b]})
    proposals.submit(world, "add_event", payload={"title": "只有乙", "involves": [b]})
    data = perspective.subject_lanes(world)
    lane = {s["name"]: [ev["title"] for _, ev in s["events"]] for s in data["subjects"]}
    assert lane["甲"] == ["只有甲", "甲和乙"]
    assert lane["乙"] == ["甲和乙", "只有乙"]


def test_changes_target_counts_as_subject_event(world):
    a = _char(world, "甲")
    proposals.submit(world, "add_event", payload={"title": "变故", "involves": [],
                                                 "changes": [{"op": "update_character", "target": a,
                                                              "payload": {"summary": "新"}}]})
    data = perspective.subject_lanes(world)
    assert [s["name"] for s in data["subjects"]] == ["甲"]


def test_detail_lists_companions_and_changes(world):
    a = _char(world, "甲")
    b = _char(world, "乙")
    proposals.submit(world, "add_event", payload={"title": "会面", "involves": [a, b],
                                                 "changes": [{"op": "update_character", "target": a,
                                                              "payload": {"motive": "复仇"}}]})
    data = perspective.subject_lanes(world)
    items = perspective.subject_detail(data, a)
    assert items[0]["with"] == ["乙"]
    assert items[0]["changes"][0]["payload"] == {"motive": "复仇"}
    assert items[0]["changes"][0]["op"] == "update_character"


def test_svg_has_one_lane_per_subject_and_escapes(world):
    a = _char(world, "<b>甲</b>")
    proposals.submit(world, "add_event", payload={"title": "a<script>", "involves": [a]})
    svg = perspective.lanes_svg(perspective.subject_lanes(world))
    assert svg.startswith("<svg") and svg.count("<polyline") == 0
    assert "<script>" not in svg and "&lt;b&gt;" in svg


def test_empty_world_has_no_lanes(world):
    assert "还没有" in perspective.lanes_svg(perspective.subject_lanes(world))


def test_events_at_same_time_are_spread_not_stacked(world):
    import re
    a = _char(world, "甲")
    b = _char(world, "乙")
    # 同一时间（默认 minute=0）的两个事件，都涉及甲和乙
    proposals.submit(world, "add_event", payload={"title": "先", "involves": [a, b]})
    proposals.submit(world, "add_event", payload={"title": "后", "involves": [a, b]})
    svg = perspective.lanes_svg(perspective.subject_lanes(world))
    # 每个泳道各有两个圆圈，两个圆圈的横坐标必须不同（不叠在一起）
    rows = re.findall(r"<circle cx='([0-9.]+)' cy='([0-9.]+)' r='11'", svg)
    by_row = {}
    for x, y in rows:
        by_row.setdefault(y, []).append(float(x))
    assert len(by_row) == 2 and all(len(v) == 2 for v in by_row.values())
    assert all(len(set(v)) == 2 for v in by_row.values()), "同一泳道内同一时间的事件应错开"
    # 同一事件在不同泳道中的横坐标一致
    xs_sorted = [sorted(v) for v in by_row.values()]
    assert xs_sorted[0] == xs_sorted[1]
