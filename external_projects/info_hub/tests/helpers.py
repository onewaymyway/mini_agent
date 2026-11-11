"""测试辅助：在临时目录中构造一个最小可运行的 info_hub 项目。"""
import json
import tempfile
from pathlib import Path

import yaml

SETTINGS = {
    "paths": {
        "sources_dir": "sources",
        "data_dir": "data",
        "reports_dir": "reports",
        "browser_profile_dir": "browser/profile",
    },
    "defaults": {"max_items": 50},
    "reports": [],
}
CATEGORIES = {"categories": [{"id": "news", "name": "资讯"}, {"id": "hot_community", "name": "热点社区"}]}


def make_project(sources: dict) -> Path:
    """sources: {source_id: source_dict} 写入临时项目，返回项目根目录。"""
    root = Path(tempfile.mkdtemp(prefix="info_hub_test_"))
    (root / "config").mkdir()
    (root / "config" / "settings.yaml").write_text(yaml.safe_dump(SETTINGS, allow_unicode=True), encoding="utf-8")
    (root / "config" / "categories.yaml").write_text(yaml.safe_dump(CATEGORIES, allow_unicode=True), encoding="utf-8")
    (root / "sources").mkdir()
    index = {"version": 1, "sources": []}
    for sid, cfg in sources.items():
        d = root / "sources" / sid
        d.mkdir(parents=True)
        (d / "source.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        index["sources"].append({"id": sid, "enabled": cfg.get("enabled", True)})
    (root / "sources" / "_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return root
