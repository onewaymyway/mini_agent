from article_to_video.source.ascii_graph import classify_code


def test_vertical_chain_with_arrow_lines():
    info = classify_code(["现实世界", "    ↓", "信息化", "    ↓", "数字化"])
    assert info["shape"] == "chain"
    assert info["nodes"] == ["现实世界", "信息化", "数字化"]
    assert info["loop"] is False


def test_inline_arrows_are_split():
    info = classify_code(["资本 → AI → 资本扩大资本"])
    assert info["shape"] == "chain"
    assert info["nodes"] == ["资本", "AI", "资本扩大资本"]


def test_loop_detected_when_node_repeats():
    info = classify_code(["超级AI … → 数据中心 → 超级AI"])
    assert info["shape"] == "chain"
    assert info["loop"] is True


def test_lone_ellipsis_is_not_a_node_and_not_a_loop():
    info = classify_code(["A", "↓", "……", "↓", "B", "↓", "……"])
    assert info["nodes"] == ["A", "B"]
    assert info["loop"] is False


def test_box_drawing_is_ascii_art():
    assert classify_code(["┌──┐", "│ A │", "└──┘"])["shape"] == "ascii_art"


def test_plain_text_and_empty():
    assert classify_code(["普通说明文字"])["shape"] == "text"
    assert classify_code(["", "  "])["shape"] == "empty"
