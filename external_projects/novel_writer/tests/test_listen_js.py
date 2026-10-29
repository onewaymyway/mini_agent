"""听书播放器前端的起始位置解析（纯函数 resolveStartIdx）：用 node 直接执行页面里的函数；没有 node 时跳过。"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "ui" / "listen_component" / "index.html"

CASES = r'''
const D={segments:["h1","a1","a2","h2","b1","b2","b3","h3","c1","c2"],chapters:[{ch:1,start:0},{ch:2,start:3},{ch:3,start:7}]};
const eq=(x,y,m)=>{ if(x!==y){console.log("FAIL "+m+" got "+x+" want "+y);process.exit(1)} };
eq(resolveStartIdx(D,{resume_ch:2,resume_seg:2}),5,"原位续播");
eq(resolveStartIdx(D,{resume_ch:3,resume_seg:99}),9,"越界夹到该章末段");
eq(resolveStartIdx(D,{resume_ch:null}),0,"从未保存：从头");
eq(resolveStartIdx(D,{resume_ch:5,resume_seg:3}),7,"保存章不在载荷里：最近的前一章开头，而不是全书开头");
const D2={segments:["h1","a1","a2","h2","NEW","b1","b2","b3"],chapters:[{ch:1,start:0},{ch:2,start:3}]};
eq(resolveStartIdx(D2,{resume_ch:2,resume_seg:3,resume_anchor:"b2"}),6,"章节改写后用锚点重新定位");
eq(resolveStartIdx(D2,{resume_ch:2,resume_seg:1,resume_anchor:"zzz"}),4,"锚点找不到则沿用段落下标");
console.log("OK");
'''


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")
def test_resolve_start_idx(tmp_path):
    js = re.search(r"<script>(.*)</script>", HTML.read_text(encoding="utf-8"), re.S).group(1)
    fn = re.search(r"  function resolveStartIdx.*?\n  }\n", js, re.S)
    assert fn, "页面里找不到 resolveStartIdx"
    script = tmp_path / "t.js"
    script.write_text(fn.group(0) + CASES, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")
def test_page_script_is_syntactically_valid(tmp_path):
    js = re.search(r"<script>(.*)</script>", HTML.read_text(encoding="utf-8"), re.S).group(1)
    script = tmp_path / "page.js"
    script.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
