from novel_writer import skills_catalog as C
from novel_writer.config import SKILLS_DIR


def test_catalog_matches_skills_dir_exactly():
    dirs = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
    assert len(dirs) == 23
    assert dirs == set(C.SKILL_CATALOG)


def test_every_skill_has_valid_frontmatter_and_executor():
    rows = C.list_skills()
    for r in rows:
        assert r["present"] and r["catalogued"], r["name"]
        assert r["name_matches_dir"], r["name"]
        assert r["description"], r["name"]
        assert r["executor"] in (C.PYTHON, C.LLM, C.HYBRID)
        assert r["pages"], r["name"]


def test_executor_split_matches_plan():
    by = {}
    for n, m in C.SKILL_CATALOG.items():
        by.setdefault(m["executor"], []).append(n)
    assert set(by[C.PYTHON]) == {"workspace-manager", "novel-director", "memory-manager"}
    assert set(by[C.HYBRID]) == {"plot-tracker", "style-guardian", "word-count-controller"}


def test_parse_frontmatter():
    assert C.parse_frontmatter("---\nname: a\ndescription: b c\n---\nbody") == {"name": "a", "description": "b c"}
    assert C.parse_frontmatter("no frontmatter") == {}
