from novel_writer import tool_api
ws = "data/novels/逆天小贩_202610171209_2"
dry_run=True
# print(tool_api.migrate_blueprint(ws, dry_run=dry_run,use_llm=True))
# print(tool_api.migrate_characters(ws, dry_run=dry_run,use_llm=True))
# print(tool_api.migrate_plot_ledger(ws, dry_run=dry_run))

# cd external_projects/novel_writer
# python do_adpt.py


import json
from pathlib import Path
from novel_writer.workspace import Workspace

ws = Workspace(ws)
p = ws.path("plot_legacy")
print("路径:", p, "存在:", p.is_file())
raw = p.read_bytes()
print("前 80 字节:", raw[:80])
d = json.loads(raw.decode("utf-8-sig"))
print("顶层类型:", type(d).__name__)
if isinstance(d, dict):
    print("顶层键:", list(d.keys()))