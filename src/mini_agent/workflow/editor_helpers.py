"""
workflow/editor_helpers.py — 看板工作流可视化编辑器的后端纯函数层
（next_doc/workflow_visual_editor_plan.md §5，里程碑 M2）

与 `api_helpers.py` 同一分层思路：本模块只做"真正的事"，返回结构化 dict，
`api/routes.py` 只做薄封装（开关/鉴权/错误映射）。不依赖 `@tool`、不 import FastAPI。

为什么不能复用 `WorkflowStore.save()`（方案 §2.2）：
  save() 是「加载 → WorkflowDef → to_dict() → yaml.dump」的往返，四处有损——
  include 被摊平、script_path 变绝对路径、prompt_file 正文写不回文件、注释与格式丢失。
  编辑器因此直接编辑**原始 YAML 文档**：
    - 读：ruamel round-trip 读原文 → 转成纯 JSON 草稿（include 条目原样保留）；
    - 校验：只在校验时按「加载同一路径」构造 WorkflowDef（展开 include、解析 script_path、
      用草稿里的 prompt_file 正文覆盖），复用 `WorkflowStore.validate_def()`；
    - 写：把草稿**同步进**原文档（按 step id 原位同步，未变化的键连同注释原样保留），
      再原子写盘。

保存护栏（§5.4）：开关 → 乐观锁（base_hash）→ 校验 → 备份 → 同步 + 原子写 → prompt_file 写回。
额外的最后一道保险：写盘前把生成的 YAML 文本重新解析，必须与草稿**语义完全一致**，
否则拒绝写入（`sync_mismatch`），绝不落一份与用户所见不一致的文件。

降级：未安装 `ruamel.yaml` 时回退 PyYAML 整体重写（会丢注释、规范化格式），
校验结果里明确警告，且原文件带注释时要求调用方显式确认（`confirm_comment_loss=True`）。
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .api_helpers import WorkflowApiError, load_store

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


# ── 常量 ──────────────────────────────────────────────────────────────────

BACKUP_DIR = ".agent/workflow_backups"
_BACKUP_ID_RE = re.compile(r"^\d{8}-\d{6}-\d{3}(-\d+)?$")
# 校验错误文案里的"步骤 'xxx'"（schema.validate 里统一用 {step.id!r} 格式）
_STEP_IN_ERROR_RE = re.compile(r"步骤\s*['\"]([^'\"]+)['\"]")

# ruamel 输出缩进的候选组合 (mapping, sequence, offset)：按原文件实际风格挑最贴近的一组，
# 让"只改一个字段"时其余行字节不变（方案 §5.3 第 6 点给的固定参数只是其中一组候选）。
_INDENT_CANDIDATES = (
    (2, 4, 2),   # steps:\n  - id: x      （手写常见风格，方案默认）
    (2, 2, 0),   # steps:\n- id: x        （PyYAML 输出风格）
    (4, 6, 2),
    (4, 4, 0),
    (4, 8, 4),
)


class WorkflowEditorError(WorkflowApiError):
    """编辑器专用结构化错误：在 WorkflowApiError(code, message) 之上多带一份 payload
    （如校验失败时的 errors_by_step、冲突时的 current_hash），供 REST 层原样透传给看板。

    code 取值：not_found / editor_disabled(403) / conflict(409) / needs_confirm(409) /
    validation_failed(422) / invalid_yaml(422) / bad_request(400) / path_escape(422) /
    sync_mismatch(500) / already_exists(409，M5 新建 / 复制时重名)。
    """

    def __init__(self, code: str, message: str, **payload: Any) -> None:
        super().__init__(code, message)
        self.payload = payload


# ── 基础工具 ──────────────────────────────────────────────────────────────

def has_ruamel() -> bool:
    """是否安装了 ruamel.yaml（未安装则走 PyYAML 降级路径）。"""
    try:
        import ruamel.yaml  # noqa: F401
        return True
    except ImportError:
        return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    """与 WorkflowStore._path 同一套命名规范化规则。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _decode(raw: bytes) -> str:
    text = raw.decode("utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n")


def _to_plain(obj: Any) -> Any:
    """把 ruamel / PyYAML 的解析结果转成纯 JSON 友好的 dict/list/标量。"""
    if isinstance(obj, dict):
        return {(k if isinstance(k, str) else str(k)): _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    if obj is None:
        return None
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if isinstance(obj, str):
        return str(obj)
    return str(obj)  # 日期/时间戳等非 JSON 类型统一转字符串


def _plain_equal(a: Any, b: Any) -> bool:
    """语义等价比较：dict 不看键顺序，数字 1 == 1.0，但 bool 与数字严格区分。"""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_plain_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_plain_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


_BLOCK_SCALAR_RE = re.compile(r"[:\-]\s+[|>][+-]?\d*\s*(#.*)?$")
_TRAILING_COMMENT_RE = re.compile(r"""^[^'"#\n]*\S\s+#""")


def _count_comment_lines(text: str) -> int:
    """粗略统计 YAML 文本里含注释的行数（跳过 `|` / `>` 块标量正文，避免把 prompt 里的
    Markdown `# 标题` 误判成注释）。只用于展示与"是否会丢注释"的提示，不参与写入逻辑。"""
    count = 0
    block_indent: Optional[int] = None
    for line in text.split("\n"):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if not stripped or indent > block_indent:
                continue  # 仍在块标量正文内
            block_indent = None
        if stripped.startswith("#"):
            count += 1
            continue
        if _TRAILING_COMMENT_RE.match(line):
            count += 1
        if _BLOCK_SCALAR_RE.search(line):
            block_indent = indent
    return count


# ── ruamel round-trip 读写 ────────────────────────────────────────────────

def _new_ruamel(mapping: int = 2, sequence: int = 4, offset: int = 2):
    from ruamel.yaml import YAML

    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096          # 放大避免长行被折断（方案 §5.3 第 6 点）
    y.allow_unicode = True
    y.default_flow_style = False
    y.indent(mapping=mapping, sequence=sequence, offset=offset)
    return y


def _ruamel_dump(y, doc: Any) -> str:
    buf = io.StringIO()
    y.dump(doc, buf)
    text = buf.getvalue()
    return text if text.endswith("\n") else text + "\n"


def _load_rt(text: str):
    y = _new_ruamel()
    return y.load(text)


def _pick_ruamel_for(text: str, doc: Any):
    """按原文件缩进风格挑输出参数：对未改动的文档做一次 dump，选与原文逐行相同数最多的一组。"""
    best, best_score = None, -1
    orig_lines = text.rstrip("\n").split("\n")
    for m, s, o in _INDENT_CANDIDATES:
        try:
            y = _new_ruamel(m, s, o)
            out = _ruamel_dump(y, doc).rstrip("\n").split("\n")
        except Exception:
            continue
        score = sum(1 for a, b in zip(orig_lines, out) if a == b)
        if score > best_score:
            best, best_score = y, score
    return best or _new_ruamel()


def _parse_plain(text: str) -> Any:
    """把 YAML 文本解析成纯 JSON 结构（校验/回读用；优先 ruamel，其次 PyYAML）。"""
    if has_ruamel():
        return _to_plain(_load_rt(text))
    import yaml  # type: ignore
    return _to_plain(yaml.safe_load(text))


# ── 草稿 → 原文档 的原位同步（方案 §5.3）───────────────────────────────────

def _step_key(item: Any) -> Optional[str]:
    """step 的匹配键：id；没有 id 的 include 条目用 `include:<片段名>`。"""
    if not isinstance(item, dict):
        return None
    sid = item.get("id")
    if sid not in (None, ""):
        return str(sid)
    if item.get("include"):
        return f"include:{item['include']}"
    return None


def _to_rt(value: Any) -> Any:
    """把纯 JSON 值转成可插入 ruamel 文档的新节点（多行字符串用 `|` 块样式，标量列表用流式）。"""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    from ruamel.yaml.scalarstring import LiteralScalarString

    if isinstance(value, dict):
        m = CommentedMap()
        for k, v in value.items():
            m[k] = _to_rt(v)
        return m
    if isinstance(value, list):
        s = CommentedSeq()
        for v in value:
            s.append(_to_rt(v))
        if value and all(not isinstance(v, (dict, list)) and not (isinstance(v, str) and "\n" in v) for v in value):
            if len(json.dumps(value, ensure_ascii=False)) <= 80:
                s.fa.set_flow_style()
        return s
    if isinstance(value, str) and "\n" in value:
        return LiteralScalarString(value)
    return value


def _new_scalar_like(old: Any, new: Any) -> Any:
    """标量值变化时的新值：尽量沿用旧值的引号样式；多行统一 `|` 块样式。"""
    from ruamel.yaml.scalarstring import LiteralScalarString, ScalarString

    if isinstance(new, str):
        if "\n" in new:
            return LiteralScalarString(new)
        if isinstance(old, ScalarString) and not isinstance(old, LiteralScalarString):
            try:
                return type(old)(new)
            except Exception:
                return new
        return new
    return _to_rt(new)


def _sync_value(old: Any, new: Any) -> Any:
    """把 new 同步进 old，返回应该存回去的值：能原位改就原位改并返回 old 本身。"""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if isinstance(old, CommentedMap) and isinstance(new, dict):
        _sync_map(old, new)
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list):
        _sync_seq(old, new)
        return old
    if _plain_equal(_to_plain(old), new):
        return old  # 值没变：保留原节点（连同引号/块样式/行尾注释）
    return _new_scalar_like(old, new)


def _sync_map(old, new: dict, *, before_key: Optional[str] = None, protect: tuple = ()) -> None:
    for k in [k for k in list(old.keys()) if k not in new and k not in protect]:
        del old[k]
    for k, v in new.items():
        if k in old:
            nv = _sync_value(old[k], v)
            if nv is not old[k]:
                old[k] = nv
        else:
            rv = _to_rt(v)
            if before_key is not None and before_key in old:
                old.insert(list(old.keys()).index(before_key), k, rv)
            else:
                old[k] = rv


def _sync_seq(old, new: list) -> None:
    n = min(len(old), len(new))
    for i in range(n):
        nv = _sync_value(old[i], new[i])
        if nv is not old[i]:
            old[i] = nv
    while len(old) > len(new):
        old.pop()
    for v in new[len(old):]:
        old.append(_to_rt(v))


def _sync_steps(old, new: list, renames: Optional[dict] = None) -> None:
    """steps 按 id 匹配（方案 §5.3 第 3 点）：已存在的 step 原位递归同步（对象本身不重建，
    其附带注释跟随）；新 step 追加；被删 step 移除；顺序按草稿重排。

    renames：{旧 id: 新 id}，由调用方（看板"改 id"）声明，使改名的 step 仍能匹配到原节点、
    保住它的注释，而不是被当成"删一个、加一个"。"""
    renames = renames or {}
    old_keys = [_step_key(x) for x in old]
    used: set[int] = set()
    plan: list[tuple] = []
    for item in new:
        key = _step_key(item)
        match_idx: Optional[int] = None
        # 先按（可能的）旧 id 匹配：renames 反查 → 再按同名 id 匹配
        candidates = [k for k, v in renames.items() if v == key] + [key]
        for cand in candidates:
            for i, k in enumerate(old_keys):
                if i not in used and k is not None and k == cand:
                    match_idx = i
                    break
            if match_idx is not None:
                break
        if match_idx is None:
            plan.append(("new", item))
        else:
            used.add(match_idx)
            plan.append(("old", old[match_idx], item))

    for i in sorted((i for i in range(len(old)) if i not in used), reverse=True):
        del old[i]

    for entry in plan:
        if entry[0] == "old":
            _sync_map(entry[1], entry[2])

    for pos, entry in enumerate(plan):
        if entry[0] == "old":
            obj = entry[1]
            if pos < len(old) and old[pos] is obj:
                continue
            j = next(i for i in range(pos, len(old)) if old[i] is obj)
            old.pop(j)
            old.insert(pos, obj)
        else:
            old.insert(pos, _to_rt(entry[1]))


def sync_draft_into_document(doc: Any, draft: dict, renames: Optional[dict] = None) -> Any:
    """把纯 JSON 草稿同步进 ruamel 文档（原位修改并返回）。"""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if doc is None:
        doc = CommentedMap()
    rest = {k: v for k, v in draft.items() if k != "steps"}
    _sync_map(doc, rest, before_key="steps", protect=("steps",))
    if "steps" in draft:
        if "steps" in doc and isinstance(doc["steps"], CommentedSeq) and isinstance(draft["steps"], list):
            _sync_steps(doc["steps"], draft["steps"], renames)
        else:
            doc["steps"] = _to_rt(draft["steps"])
    elif "steps" in doc:
        del doc["steps"]
    return doc


def _dump_pyyaml(draft: dict) -> str:
    """降级路径：PyYAML 整体重写（丢注释）。多行字符串仍尽量用 `|` 块样式。"""
    import yaml  # type: ignore

    class _Dumper(yaml.SafeDumper):
        pass

    def _str_repr(dumper, data):
        style = "|" if "\n" in data else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)

    _Dumper.add_representer(str, _str_repr)
    return yaml.dump(draft, Dumper=_Dumper, allow_unicode=True, default_flow_style=False,
                     sort_keys=False, indent=2, width=4096)


# ── 定位 / 读取 ───────────────────────────────────────────────────────────

def _require_enabled(cfg: "AppConfig") -> None:
    wf_cfg = getattr(cfg, "workflow", None)
    if not bool(getattr(wf_cfg, "visual_editor_enabled", True)):
        raise WorkflowEditorError(
            "editor_disabled",
            "工作流可视化编辑器的写入功能已关闭。如需开启，请在 agent_config.json 的 "
            "workflow 段设置 \"visual_editor_enabled\": true（只读查看不受影响）。",
        )


def _locate(cfg: "AppConfig", name: str):
    store = load_store(cfg)
    path = store.resolve_path(name)
    if path is None:
        raise WorkflowEditorError("not_found", f"找不到工作流 {name!r}")
    mode = "dir" if (path.name == "workflow.yaml" and path.parent != store._dir) else "file"
    return store, path, mode


def _project_root(cfg: "AppConfig") -> Path:
    return Path(cfg.project_root)


def _rel(cfg: "AppConfig", path: Path) -> str:
    try:
        return str(path.relative_to(_project_root(cfg))).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_in(base_dir: Path, rel: str) -> Optional[Path]:
    """解析相对路径，必须落在 base_dir 内，否则返回 None（与 _resolve_script_paths 同一标准）。"""
    try:
        target = (base_dir / rel).resolve()
        target.relative_to(base_dir.resolve())
        return target
    except (ValueError, OSError):
        return None


def _read_document(path: Path) -> tuple[bytes, str, Any, dict]:
    """读原文件 → (原始字节, 文本, ruamel 文档或 None, 纯 JSON dict)。"""
    raw = path.read_bytes()
    text = _decode(raw)
    try:
        if has_ruamel():
            doc = _load_rt(text)
            plain = _to_plain(doc)
        else:
            import yaml  # type: ignore
            doc = None
            plain = _to_plain(yaml.safe_load(text))
    except Exception as e:
        raise WorkflowEditorError("invalid_yaml", f"工作流 YAML 无法解析：{e}")
    if plain is None:
        plain = {}
    if not isinstance(plain, dict):
        raise WorkflowEditorError("invalid_yaml", "工作流 YAML 顶层必须是映射（name/steps/...）")
    return raw, text, doc, plain


def _collect_prompt_files(base_dir: Path, draft: dict) -> tuple[dict, dict, list[str]]:
    """收集各 step.prompt_file 的正文与字节 hash；返回 (正文, hash, 警告)。"""
    bodies: dict[str, str] = {}
    hashes: dict[str, str] = {}
    warnings: list[str] = []
    for s in draft.get("steps", []) or []:
        if not isinstance(s, dict) or s.get("include"):
            continue
        rel = s.get("prompt_file")
        if not rel or not isinstance(rel, str) or rel in bodies:
            continue
        target = _resolve_in(base_dir, rel)
        if target is None:
            warnings.append(f"步骤 {s.get('id')!r} 的 prompt_file 越出了 workflow 目录范围：{rel!r}")
            continue
        if not target.is_file():
            warnings.append(f"步骤 {s.get('id')!r} 的 prompt_file 文件不存在：{rel!r}")
            continue
        try:
            data = target.read_bytes()
            bodies[rel] = _decode(data)
            hashes[rel] = _sha256(data)
        except Exception as e:
            warnings.append(f"步骤 {s.get('id')!r} 的 prompt_file 读取失败：{rel!r}（{e}）")
    return bodies, hashes, warnings


def load_for_edit(cfg: "AppConfig", name: str) -> dict:
    """[GET /v1/workflows/{name}/editor] 编辑用文档。

    返回 {name, path, mode(file|dir), draft, base_hash, comment_lines, has_ruamel,
          editor_enabled, prompt_files, prompt_hashes, warnings}。
    editor_enabled 为 false 时看板应隐藏/禁用保存类操作（写入端点会返回 403）。
    draft 是原始文档转成的纯 JSON（include 条目原样保留、script_path 保持相对写法）；
    prompt_files 是各 prompt_file 相对路径 → 正文。只读，不受写入开关影响。
    """
    _store, path, mode = _locate(cfg, name)
    raw, text, _doc, plain = _read_document(path)
    bodies, hashes, warnings = _collect_prompt_files(path.parent, plain)
    return {
        "name": name,
        "path": _rel(cfg, path),
        "mode": mode,
        "draft": plain,
        "base_hash": _sha256(raw),
        "comment_lines": _count_comment_lines(text),
        "has_ruamel": has_ruamel(),
        "editor_enabled": bool(getattr(getattr(cfg, "workflow", None), "visual_editor_enabled", True)),
        "prompt_files": bodies,
        "prompt_hashes": hashes,
        "warnings": warnings,
    }


# ── 草稿规范化 / 校验 ─────────────────────────────────────────────────────

def _normalize_draft(draft: Any, disk_plain: dict, renames: Optional[dict] = None) -> dict:
    """深拷贝并清洗草稿：值为 None 的顶层/step 级键视为"已清空"——丢弃该键（原文档里没有它
    则避免写成 `key: null` 造成无谓 diff，也避免 `int(None)` 之类的类型错误；原文档里有它
    则等价于删除该字段）。唯一例外：原文档里本来就显式写着 `key: null` 且草稿未动它，保持不变。
    嵌套数据（params / tool_args / defaults 内部）里的 null 是用户数据，原样保留。"""
    if not isinstance(draft, dict):
        raise WorkflowEditorError("bad_request", "draft 必须是对象（工作流 YAML 的 JSON 形式）")
    d = _to_plain(copy.deepcopy(draft))
    for k in [k for k, v in d.items() if v is None and not (k in disk_plain and disk_plain[k] is None)]:
        del d[k]
    steps = d.get("steps")
    if isinstance(steps, list):
        renames = renames or {}
        old_by_key = {_step_key(s): s for s in (disk_plain.get("steps") or []) if isinstance(s, dict)}
        for s in steps:
            if not isinstance(s, dict):
                continue
            key = _step_key(s)
            old = old_by_key.get(key)
            if old is None:
                for ok, nk in renames.items():
                    if nk == key:
                        old = old_by_key.get(ok)
                        break
            old = old or {}
            for k in [k for k, v in s.items() if v is None and not (k in old and old[k] is None)]:
                del s[k]
    return d


def _find_cycle(steps: list) -> Optional[list[str]]:
    """在 depends_on 图里找一个环，返回形如 [a, b, c, a] 的路径；无环返回 None。
    只考虑存在的 step（依赖不存在的引用由 validate() 单独报错）。"""
    ids = {s.id for s in steps}
    deps = {s.id: [d for d in s.depends_on if d in ids] for s in steps}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in ids}
    stack: list[str] = []

    def dfs(u: str) -> Optional[list[str]]:
        color[u] = GRAY
        stack.append(u)
        for v in deps[u]:
            if color[v] == GRAY:
                return stack[stack.index(v):] + [v]
            if color[v] == WHITE:
                r = dfs(v)
                if r:
                    return r
        stack.pop()
        color[u] = BLACK
        return None

    for s in steps:
        if color[s.id] == WHITE:
            r = dfs(s.id)
            if r:
                return r
    return None


def _include_entries(draft: dict) -> dict[str, str]:
    """草稿里的 include 条目：{include 节点 id: 片段名}。"""
    out: dict[str, str] = {}
    for s in draft.get("steps", []) or []:
        if isinstance(s, dict) and s.get("include"):
            out[str(s.get("id") or s["include"])] = str(s["include"])
    return out


def _attribute(step_id: str, raw_ids: set[str], includes: dict[str, str]) -> str:
    """把（可能是 include 展开后带 `<id>__` 前缀的）step id 归到画布上真实存在的节点。"""
    if step_id in raw_ids:
        return step_id
    for inc in includes:
        if step_id.startswith(inc + "__"):
            return inc
    return step_id


def _known_roles(cfg: "AppConfig", source_dir: Optional[Path] = None) -> dict[str, Any]:
    """已注册的角色 profile：全局 + 项目级（+ 目录模式 workflow 本地 agents/，本地同名覆盖）。"""
    try:
        from mini_agent.orchestrator.agent_profiles import AgentProfileLoader
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(_project_root(cfg))
        dirs = [paths.global_agents_dir, paths.project_agents_dir]
        if source_dir is not None:
            dirs.append(Path(source_dir) / "agents")
        loader = AgentProfileLoader(dirs)
        return {n: loader.get(n) for n in loader.available}
    except Exception:
        return {}


def _build_def(cfg, store, path: Path, mode: str, draft: dict, prompt_files: dict):
    """按"加载同一路径"的口径由草稿构造 WorkflowDef。返回 (wf, errors, warnings)。
    errors/warnings 里是构造阶段就能发现的问题（片段缺失、路径越界、字段类型错误等）。"""
    from .schema import WorkflowDef

    errors: list[str] = []
    warnings: list[str] = []
    data = copy.deepcopy(draft)

    # 片段引用：先自行检查，缺失时归到 include 节点，而不是让 _expand_includes 抛无归属的异常
    for inc_id, snippet in _include_entries(data).items():
        try:
            store.load_snippet(snippet)
        except Exception as e:
            errors.append(f"步骤 {inc_id!r} 引用的可复用片段不可用：{e}")
    if errors:
        return None, errors, warnings

    try:
        store._expand_includes(data)
    except Exception as e:
        return None, [f"include 展开失败：{e}"], warnings

    try:
        wf = WorkflowDef.from_dict(data)
    except Exception as e:
        # 定位到具体 step，便于画布上标红
        bad = []
        for s in data.get("steps", []) or []:
            if not isinstance(s, dict):
                continue
            try:
                WorkflowDef.from_dict({"steps": [s]})
            except Exception as se:
                bad.append(f"步骤 {str(s.get('id', ''))!r} 的字段类型有误：{se}")
        return None, bad or [f"工作流定义字段类型有误：{e}"], warnings

    base_dir = path.parent
    if mode == "dir":
        wf.source_dir = path.parent

    for step in wf.steps:
        if step.prompt_file:
            target = _resolve_in(base_dir, step.prompt_file)
            if target is None:
                errors.append(f"步骤 {step.id!r} 的 prompt_file 越出了 workflow 目录范围：{step.prompt_file!r}")
            elif step.prompt_file in prompt_files:
                step.prompt = str(prompt_files[step.prompt_file])
            elif target.is_file():
                try:
                    step.prompt = _decode(target.read_bytes())
                except Exception as e:
                    warnings.append(f"步骤 {step.id!r} 的 prompt_file 读取失败：{step.prompt_file!r}（{e}）")
            else:
                warnings.append(f"步骤 {step.id!r} 的 prompt_file 文件不存在且草稿未提供正文：{step.prompt_file!r}")
        if step.script_path:
            target = _resolve_in(base_dir, step.script_path)
            if target is None:
                errors.append(f"步骤 {step.id!r} 的 script_path 越出了 workflow 目录范围：{step.script_path!r}")
            else:
                step.script_path = str(target)
    return wf, errors, warnings


def _group_by_step(messages: list[str], raw_ids: set[str], includes: dict[str, str]) -> tuple[dict, list[str]]:
    by_step: dict[str, list[str]] = {}
    unattributed: list[str] = []
    for m in messages:
        hit = _STEP_IN_ERROR_RE.search(m)
        if hit:
            by_step.setdefault(_attribute(hit.group(1), raw_ids, includes), []).append(m)
        else:
            unattributed.append(m)
    return by_step, unattributed


def validate_draft(
    cfg: "AppConfig",
    name: str,
    draft: dict,
    prompt_files: Optional[dict] = None,
    renames: Optional[dict] = None,
) -> dict:
    """[POST /v1/workflows/{name}/editor/validate] 草稿校验（不落盘，不受写入开关影响）。

    返回 {ok, errors, warnings, errors_by_step, warnings_by_step, workflow_errors,
          cycle, batches, has_ruamel, will_lose_comments}。
      - errors_by_step：由错误文案里的 `步骤 '<id>'` 解析得到；include 展开出来的
        `<id>__xxx` 归到 include 节点上。
      - workflow_errors：无法归到具体 step 的错误（如整体名称被改）。
      - batches：按并发批次分层的画布节点 id（include 节点落在其最早成员所在批次）；
        存在环 / 依赖缺失时为 None。
    """
    store, path, mode = _locate(cfg, name)
    _raw, text, _doc, disk_plain = _read_document(path)
    d = _normalize_draft(draft, disk_plain, renames)
    prompt_files = dict(prompt_files or {})

    errors: list[str] = []
    warnings: list[str] = []
    cycle: Optional[list[str]] = None
    batches: Optional[list[list[str]]] = None

    if not isinstance(d.get("steps", []), list) or any(not isinstance(s, dict) for s in d.get("steps", []) or []):
        return _validation_result(
            ["steps 必须是对象列表"], [], set(), {}, None, None,
        )
    if d.get("name") != disk_plain.get("name"):
        errors.append(
            f"工作流名称不能在编辑器中修改（磁盘上为 {disk_plain.get('name')!r}，草稿为 {d.get('name')!r}）"
        )

    raw_ids = {_step_key(s) or "" for s in d.get("steps", []) or []}
    raw_ids |= {str(s.get("id")) for s in d.get("steps", []) or [] if s.get("id")}
    includes = _include_entries(d)

    wf, build_errors, build_warnings = _build_def(cfg, store, path, mode, d, prompt_files)
    errors.extend(build_errors)
    warnings.extend(build_warnings)

    if wf is not None:
        errors.extend(store.validate_def(wf, cfg=cfg))
        warnings.extend(list(getattr(wf, "last_validate_warnings", []) or []))

        cycle_path = _find_cycle(wf.steps)
        if cycle_path:
            cycle = [_attribute(i, raw_ids, includes) for i in cycle_path]
            errors.append(f"步骤 {cycle[0]!r} 处于循环依赖中：{' → '.join(cycle)}")

        _extra_warnings(cfg, wf, mode, path, warnings)

        blocked = bool(cycle) or any("依赖不存在的步骤" in e for e in errors)
        if not blocked:
            try:
                from .runner import WorkflowRunner
                layers = WorkflowRunner(cfg)._compute_parallel_batches(wf)
                seen: set[str] = set()
                batches = []
                for layer in layers:
                    ids: list[str] = []
                    for s in layer:
                        node = _attribute(s.id, raw_ids, includes)
                        if node not in seen:
                            seen.add(node)
                            ids.append(node)
                    if ids:
                        batches.append(ids)
            except Exception:
                batches = None

    will_lose = False
    if not has_ruamel():
        cnt = _count_comment_lines(text)
        will_lose = cnt > 0
        warnings.append(
            "未安装 ruamel.yaml，本次保存将回退为 PyYAML 整体重写：会规范化格式"
            + (f"，并丢失原文件中的注释（约 {cnt} 行）" if cnt else "")
            + "。建议 `pip install ruamel.yaml`。"
        )

    return _validation_result(errors, warnings, raw_ids, includes, cycle, batches, will_lose)


def _validation_result(errors, warnings, raw_ids, includes, cycle, batches, will_lose=False) -> dict:
    by_step, unattributed = _group_by_step(errors, raw_ids, includes)
    if cycle:
        for node in set(cycle):  # 环上的每个节点都要能在画布上标红
            by_step.setdefault(node, [])
            msg = f"步骤 {node!r} 处于循环依赖中：{' → '.join(cycle)}"
            if msg not in by_step[node] and not any("循环依赖" in m for m in by_step[node]):
                by_step[node].append(msg)
    warn_by_step, _ = _group_by_step(warnings, raw_ids, includes)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "errors_by_step": by_step,
        "warnings_by_step": warn_by_step,
        "workflow_errors": unattributed,
        "cycle": cycle,
        "batches": batches,
        "has_ruamel": has_ruamel(),
        "will_lose_comments": will_lose,
    }


def _extra_warnings(cfg, wf, mode: str, path: Path, warnings: list[str]) -> None:
    """编辑器附加的 warning 级提示（不阻断保存）：运行期开关关闭、角色未注册。
    编辑器不绕过 script_step_enabled / python_step_enabled 这两个运行期开关，只提示。"""
    wf_cfg = getattr(cfg, "workflow", None)
    script_on = bool(getattr(wf_cfg, "script_step_enabled", False))
    python_on = bool(getattr(wf_cfg, "python_step_enabled", False))
    roles: Optional[dict] = None
    if bool(getattr(wf_cfg, "validate_role_refs_on_save", True)):
        roles = _known_roles(cfg, path.parent if mode == "dir" else None)
    for s in wf.steps:
        etype = s.effective_type
        if etype == "script" and not script_on:
            warnings.append(f"步骤 {s.id!r} 是 script 类型，但当前配置 workflow.script_step_enabled=false，运行时不会执行")
        if etype == "python_step" and not python_on:
            warnings.append(f"步骤 {s.id!r} 是 python_step 类型，但当前配置 workflow.python_step_enabled=false，运行时不会执行")
        if roles and s.role and s.role not in roles:
            warnings.append(f"步骤 {s.id!r} 引用的角色 {s.role!r} 未在已注册的 Agent profile 中找到（运行时可能失败）")


# ── 保存 ──────────────────────────────────────────────────────────────────

def _changed_steps(disk_plain: dict, draft: dict, renames: Optional[dict]) -> dict:
    renames = renames or {}
    old = {_step_key(s): s for s in disk_plain.get("steps", []) or [] if isinstance(s, dict)}
    new = {_step_key(s): s for s in draft.get("steps", []) or [] if isinstance(s, dict)}
    rev = {v: k for k, v in renames.items()}
    added, modified = [], []
    matched_old: set = set()
    for key, s in new.items():
        ok = key if key in old else rev.get(key)
        if ok is None or ok not in old:
            added.append(key)
            continue
        matched_old.add(ok)
        if ok != key or not _plain_equal(old[ok], s):
            modified.append(key)
    removed = [k for k in old if k not in matched_old]
    old_order = [k for k in old if k in matched_old]
    new_order = [rev.get(k, k) for k in new if (rev.get(k, k) in matched_old)]
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "reordered": old_order != new_order,
        "top_level": sorted(
            k for k in set(disk_plain) | set(draft)
            if k != "steps" and not _plain_equal(disk_plain.get(k), draft.get(k))
        ),
    }


def _new_backup_id(bdir: Path) -> str:
    now = time.time()
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
    bid, n = base, 0
    while (bdir / f"{bid}.yaml").exists():
        n += 1
        bid = f"{base}-{n}"
    return bid


def _backup_dir(cfg: "AppConfig", name: str) -> Path:
    return _project_root(cfg) / BACKUP_DIR / _safe_name(name)


def _prune_backups(cfg: "AppConfig", name: str) -> None:
    keep = max(1, int(getattr(getattr(cfg, "workflow", None), "editor_backup_keep", 20) or 20))
    bdir = _backup_dir(cfg, name)
    ids = sorted(p.stem for p in bdir.glob("*.yaml"))
    for bid in ids[: max(0, len(ids) - keep)]:
        try:
            (bdir / f"{bid}.yaml").unlink()
        except OSError:
            pass
        shutil.rmtree(bdir / f"{bid}.prompts", ignore_errors=True)


def _make_backup(cfg, name: str, raw: bytes, prompt_old: dict[str, bytes]) -> str:
    """备份旧文件（原样字节）到 .agent/workflow_backups/<name>/<id>.yaml；
    被改写的 prompt 文件放在同名 `<id>.prompts/<相对路径>` 下。"""
    bdir = _backup_dir(cfg, name)
    bdir.mkdir(parents=True, exist_ok=True)
    bid = _new_backup_id(bdir)
    (bdir / f"{bid}.yaml").write_bytes(raw)
    for rel, data in prompt_old.items():
        target = bdir / f"{bid}.prompts" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _prune_backups(cfg, name)
    return bid


def _render_yaml(text: str, doc: Any, draft: dict, renames: Optional[dict]) -> str:
    """生成保存用的 YAML 文本（ruamel 原位同步；无 ruamel 时 PyYAML 整体重写）。"""
    if not has_ruamel():
        return _dump_pyyaml(draft)
    y = _pick_ruamel_for(text, doc)
    fresh = _load_rt(text)          # 用干净副本同步，避免污染 doc（doc 仅用于挑缩进）
    sync_draft_into_document(fresh, draft, renames)
    return _ruamel_dump(y, fresh)


def save_draft(
    cfg: "AppConfig",
    name: str,
    draft: dict,
    prompt_files: Optional[dict] = None,
    base_hash: Optional[str] = None,
    *,
    prompt_hashes: Optional[dict] = None,
    renames: Optional[dict] = None,
    force: bool = False,
    confirm_comment_loss: bool = False,
) -> dict:
    """[PUT /v1/workflows/{name}/editor] 保存草稿（方案 §5.4）。

    顺序：开关 → 乐观锁 → 校验 → 生成新文本（含语义回读保险）→ 备份 → 原子写 yaml →
    prompt_file 写回。任何一步失败都不会留下半截文件。

    返回 {status: saved|unchanged, base_hash, changed_steps, warnings, path, backup_id,
          prompt_files_written, git_hint}。
    """
    from mini_agent.utils.atomic_write import atomic_write_text

    _require_enabled(cfg)
    store, path, mode = _locate(cfg, name)
    raw, text, doc, disk_plain = _read_document(path)
    current_hash = _sha256(raw)

    # 2. 乐观锁
    if not force:
        if not base_hash:
            raise WorkflowEditorError("bad_request", "保存需要 base_hash（先用 GET .../editor 读取；确需覆盖可传 force=true）")
        if base_hash != current_hash:
            raise WorkflowEditorError(
                "conflict",
                "该工作流文件在你打开编辑器之后已被修改（主 Agent 或其它入口）。请重新加载，或选择强制覆盖。",
                current_hash=current_hash, base_hash=base_hash,
            )

    d = _normalize_draft(draft, disk_plain, renames)
    prompt_files = {str(k): str(v) for k, v in (prompt_files or {}).items()}

    # 3. 校验
    result = validate_draft(cfg, name, d, prompt_files, renames)
    if not result["ok"]:
        raise WorkflowEditorError(
            "validation_failed",
            "校验未通过，未保存：\n" + "\n".join(f"- {e}" for e in result["errors"]),
            errors=result["errors"], errors_by_step=result["errors_by_step"],
            workflow_errors=result["workflow_errors"], cycle=result["cycle"],
        )

    base_dir = path.parent

    # 需要写回的 prompt 文件：被草稿里某个 step 引用、且正文与磁盘不同
    prompt_writes: dict[str, tuple[Path, str, Optional[bytes]]] = {}
    referenced = {s.get("prompt_file") for s in d.get("steps", []) or []
                  if isinstance(s, dict) and not s.get("include") and s.get("prompt_file")}
    for rel, body in prompt_files.items():
        if rel not in referenced:
            continue
        target = _resolve_in(base_dir, rel)
        if target is None:
            raise WorkflowEditorError("path_escape", f"prompt_file 越出了 workflow 目录范围：{rel!r}")
        old_bytes = target.read_bytes() if target.is_file() else None
        if old_bytes is not None and _decode(old_bytes) == body:
            continue
        if old_bytes is not None and not force and prompt_hashes and rel in prompt_hashes \
                and prompt_hashes[rel] != _sha256(old_bytes):
            raise WorkflowEditorError(
                "conflict", f"prompt 文件 {rel!r} 在你打开编辑器之后已被修改，请重新加载或强制覆盖。",
                current_hash=current_hash, conflicting_prompt_files=[rel],
            )
        prompt_writes[rel] = (target, body, old_bytes)

    # 生成新的 yaml 文本
    yaml_unchanged = _plain_equal(disk_plain, d)
    new_text: Optional[str] = None
    if not yaml_unchanged:
        if not has_ruamel() and _count_comment_lines(text) > 0 and not confirm_comment_loss:
            raise WorkflowEditorError(
                "needs_confirm",
                "未安装 ruamel.yaml，保存会丢失原文件中的注释。请确认后以 confirm_comment_loss=true 重试，"
                "或先 `pip install ruamel.yaml`。",
                will_lose_comments=True,
            )
        try:
            new_text = _render_yaml(text, doc, d, renames)
            back = _parse_plain(new_text)
        except WorkflowEditorError:
            raise
        except Exception as e:
            raise WorkflowEditorError("sync_mismatch", f"生成 YAML 失败，未保存：{e}")
        if not _plain_equal(back, d):
            raise WorkflowEditorError(
                "sync_mismatch", "生成的 YAML 与草稿语义不一致，已拒绝写入（原文件未被改动）。请把该情况反馈给开发者。"
            )

    changes = _changed_steps(disk_plain, d, renames)
    if yaml_unchanged and not prompt_writes:
        return {
            "status": "unchanged", "base_hash": current_hash, "changed_steps": changes,
            "warnings": result["warnings"], "path": _rel(cfg, path), "backup_id": None,
            "prompt_files_written": [], "git_hint": None,
        }

    # 4. 备份
    backup_id = _make_backup(
        cfg, name, raw,
        {rel: ob for rel, (_t, _b, ob) in prompt_writes.items() if ob is not None},
    )

    # 5. 原子写 yaml，6. 写回 prompt 文件
    if new_text is not None:
        atomic_write_text(path, new_text)
    written: list[str] = []
    for rel, (target, body, _ob) in prompt_writes.items():
        atomic_write_text(target, body)
        written.append(rel)

    new_hash = _sha256(path.read_bytes())
    hint: Optional[str] = None
    if bool(getattr(getattr(cfg, "workflow", None), "git_hint_enabled", True)):
        try:
            from . import git_integration
            hint = git_integration.save_hint(_project_root(cfg), path)
        except Exception:
            hint = None

    return {
        "status": "saved", "base_hash": new_hash, "changed_steps": changes,
        "warnings": result["warnings"], "path": _rel(cfg, path), "backup_id": backup_id,
        "prompt_files_written": written, "git_hint": hint,
    }


# ── 新建 / 复制工作流（M5，方案 §5.2 create_workflow）────────────────────

_NAME_MAX_LEN = 64
# 会被 YAML 当作非字符串解析的裸标量（数字 / 布尔 / null），写 name 时需要加引号
_YAML_NON_STRING_RE = re.compile(r"^(?:[-+]?[0-9_.]+|true|false|yes|no|on|off|null|~)$", re.IGNORECASE)
_TOP_NAME_LINE_RE = re.compile(r"^name[ \t]*:[^\n]*$", re.MULTILINE)

_BLANK_TEMPLATE = """\
# 由看板可视化编辑器创建的空白工作流：请在编辑器里补充步骤与依赖。
name: {name}
description: ""
steps:
  - id: step_1
    name: 第一步
    prompt: 请在此填写这一步要完成的任务说明。
"""


def validate_new_name(name: Any) -> str:
    """校验并返回可用于新建的工作流名。规则与 `WorkflowStore._path` 的规范化一致：
    只允许字母 / 数字（含中文）/ 下划线 / 中划线——保证「名称 == 文件名」，不会出现「输入 a b、
    实际落成 a_b」这类让用户找不到文件的静默改名。"""
    n = str(name or "").strip()
    if not n:
        raise WorkflowEditorError("bad_request", "工作流名称不能为空")
    if len(n) > _NAME_MAX_LEN:
        raise WorkflowEditorError("bad_request", f"工作流名称过长（最多 {_NAME_MAX_LEN} 个字符）")
    bad = sorted({c for c in n if not (c.isalnum() or c in "-_")})
    if bad:
        raise WorkflowEditorError(
            "bad_request",
            "工作流名称只能包含字母、数字、下划线和中划线，发现非法字符：" + " ".join(repr(c) for c in bad),
        )
    return n


def _yaml_name_scalar(name: str) -> str:
    return f'"{name}"' if _YAML_NON_STRING_RE.match(name) else name


def _set_top_level_name(text: str, new_name: str) -> str:
    """只改顶层 `name:` 那一行的值（保留其余全部文本、注释与格式）；没有该行则在文件开头补一行。
    行尾注释一并保留。"""
    scalar = _yaml_name_scalar(new_name)
    m = _TOP_NAME_LINE_RE.search(text)
    if m is None:
        return f"name: {scalar}\n" + text
    line = m.group(0)
    comment = ""
    ci = re.search(r"\s+#", line)
    if ci:
        comment = line[ci.start():]
    return text[:m.start()] + f"name: {scalar}{comment}" + text[m.end():]


def create_workflow(cfg: "AppConfig", name: str, copy_from: Optional[str] = None) -> dict:
    """[POST /v1/workflows] 新建空白工作流，或复制已有工作流（受写入开关控制）。

    - 空白：单文件模式 `<name>.yaml`，带一个合法的起始步骤（创建后立即能通过校验）。
    - 复制：保持源工作流的模式——单文件复制文本（只改顶层 `name:` 行，注释 / include / 相对路径
      原样保留）；目录模式整目录复制（agents / skills / prompts 一并带走，`__pycache__` 除外）。
    - 已存在同名工作流 → `already_exists`(409)；绝不覆盖。
    - 写完后回读校验（能加载、名字一致），失败则清理刚创建的文件并抛 `sync_mismatch`，
      不留下半成品。

    单文件复制时若源工作流有 `prompt_file` 步骤，副本与源**共享**这些文件（相对路径都指向同一
    位置）——在返回的 `warnings` 里明确提示，避免用户在副本里改 prompt 正文时悄悄改到原工作流。
    返回 {status, name, mode, path, copied_from, base_hash, warnings}。
    """
    from mini_agent.utils.atomic_write import atomic_write_text

    _require_enabled(cfg)
    new = validate_new_name(name)
    store = load_store(cfg)
    if (store.resolve_path(new) is not None or store._path(new).exists()
            or store._dir_path(new).exists()):
        raise WorkflowEditorError("already_exists", f"已存在同名工作流 {new!r}，请换一个名称")

    warnings: list[str] = []
    created_file: Optional[Path] = None
    created_dir: Optional[Path] = None
    try:
        if copy_from:
            src_path = store.resolve_path(copy_from)
            if src_path is None:
                raise WorkflowEditorError("not_found", f"找不到要复制的工作流 {copy_from!r}")
            src_is_dir = src_path.name == "workflow.yaml" and src_path.parent != store._dir
            if src_is_dir:
                dst_dir = store._dir_path(new)
                shutil.copytree(src_path.parent, dst_dir,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                created_dir = dst_dir
                entry = dst_dir / "workflow.yaml"
                atomic_write_text(entry, _set_top_level_name(_decode(entry.read_bytes()), new))
                final_path, mode = entry, "dir"
            else:
                text = _decode(src_path.read_bytes())
                final_path = store._path(new)
                atomic_write_text(final_path, _set_top_level_name(text, new))
                created_file, mode = final_path, "file"
                try:
                    import yaml  # type: ignore
                    src_doc = yaml.safe_load(text) or {}
                    shared = sorted({
                        str(s.get("prompt_file")) for s in (src_doc.get("steps") or [])
                        if isinstance(s, dict) and s.get("prompt_file")
                    })
                except Exception:
                    shared = []
                if shared:
                    warnings.append(
                        "副本与原工作流共享这些 prompt 文件（单文件模式下相对路径指向同一位置）："
                        + "、".join(shared) + "；在编辑器里修改其正文会同时影响两者。"
                    )
        else:
            final_path = store._path(new)
            atomic_write_text(final_path, _BLANK_TEMPLATE.format(name=_yaml_name_scalar(new)))
            created_file, mode = final_path, "file"

        loaded = store.load(new)
        if loaded is None or loaded.name != new:
            raise WorkflowEditorError(
                "sync_mismatch",
                f"新建后回读校验失败（加载不出工作流或名称不一致，得到 {getattr(loaded, 'name', None)!r}），已清理。",
            )
        if not copy_from:
            errs = store.validate_def(loaded, cfg)
            if errs:
                raise WorkflowEditorError("sync_mismatch", "空白模板未通过校验，已清理：" + "；".join(errs))
    except BaseException:
        if created_file is not None:
            try:
                created_file.unlink()
            except OSError:
                pass
        if created_dir is not None:
            shutil.rmtree(created_dir, ignore_errors=True)
        raise

    return {
        "status": "created", "name": new, "mode": mode, "path": _rel(cfg, final_path),
        "copied_from": copy_from or None, "base_hash": _sha256(final_path.read_bytes()),
        "warnings": warnings,
    }


# ── 单步试运行的前置检查（M5，方案 §5.5 steps/{step_id}/test）─────────────

def precheck_step_test(cfg: "AppConfig", name: str, step_id: str) -> dict:
    """试运行前先同步确认工作流与 step 存在（廉价、立即失败），真正的执行交给异步任务。
    只看**已保存**定义——与 `api_helpers.test_workflow_step` 一致。"""
    store = load_store(cfg)
    wf = store.load(name)
    if wf is None:
        raise WorkflowApiError("not_found", f"找不到工作流 {name!r}")
    step = next((s for s in wf.steps if s.id == step_id), None)
    if step is None:
        raise WorkflowApiError("bad_step", f"工作流 {name!r} 中不存在 step_id={step_id!r}")
    return {"step_type": step.effective_type}


# ── 备份列表 / 恢复（M2 实现，端点在 M5 接入）──────────────────────────────

def list_backups(cfg: "AppConfig", name: str) -> list[dict]:
    """列出某工作流的编辑器备份（新的在前）：{id, created, size, has_prompts}。"""
    bdir = _backup_dir(cfg, name)
    out: list[dict] = []
    if not bdir.is_dir():
        return out
    for p in sorted(bdir.glob("*.yaml"), reverse=True):
        bid = p.stem
        m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", bid)
        created = f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else ""
        out.append({
            "id": bid, "created": created, "size": p.stat().st_size,
            "has_prompts": (bdir / f"{bid}.prompts").is_dir(),
        })
    return out


def restore_backup(cfg: "AppConfig", name: str, backup_id: str, base_hash: Optional[str] = None) -> dict:
    """恢复某份备份。恢复前先把当前文件再备份一份（恢复本身也可撤销）；受写入开关控制。
    不做校验（备份是曾经保存过的版本）。"""
    from mini_agent.utils.atomic_write import atomic_write_text

    _require_enabled(cfg)
    if not _BACKUP_ID_RE.match(backup_id or ""):
        raise WorkflowEditorError("bad_request", f"非法的备份 id：{backup_id!r}")
    _store, path, _mode = _locate(cfg, name)
    bdir = _backup_dir(cfg, name)
    src = bdir / f"{backup_id}.yaml"
    if not src.is_file():
        raise WorkflowEditorError("not_found", f"找不到备份 {backup_id!r}")
    raw = path.read_bytes()
    if base_hash and base_hash != _sha256(raw):
        raise WorkflowEditorError("conflict", "当前文件已被修改，请重新加载后再恢复。", current_hash=_sha256(raw))

    prompts_src = bdir / f"{backup_id}.prompts"
    to_restore: list[tuple[Path, bytes, Optional[bytes]]] = []
    if prompts_src.is_dir():
        for f in sorted(p for p in prompts_src.rglob("*") if p.is_file()):
            rel = f.relative_to(prompts_src).as_posix()
            target = _resolve_in(path.parent, rel)
            if target is None:
                raise WorkflowEditorError("path_escape", f"备份中的 prompt 路径越界：{rel!r}")
            to_restore.append((target, f.read_bytes(), target.read_bytes() if target.is_file() else None))

    new_backup = _make_backup(
        cfg, name, raw,
        {t.relative_to(path.parent.resolve()).as_posix(): ob for t, _b, ob in to_restore if ob is not None},
    )
    atomic_write_text(path, _decode(src.read_bytes()))
    for target, data, _ob in to_restore:
        atomic_write_text(target, _decode(data))
    return {
        "status": "restored", "restored_from": backup_id, "backup_id": new_backup,
        "base_hash": _sha256(path.read_bytes()), "path": _rel(cfg, path),
    }


# ── 编辑器元信息（下拉选项）───────────────────────────────────────────────

def editor_meta(cfg: "AppConfig", workflow: Optional[str] = None) -> dict:
    """[GET /v1/workflow_editor/meta] 属性面板下拉选项所需的元信息（只读）。

    workflow 传入时（目录模式）额外合并该工作流本地 agents/ 与 skills/ 里的资源。
    """
    store = load_store(cfg)
    source_dir: Optional[Path] = None
    if workflow:
        p = store.resolve_path(workflow)
        if p is not None and p.name == "workflow.yaml" and p.parent != store._dir:
            source_dir = p.parent

    from .schema import STEP_TYPES
    try:
        from .executors import get_registered_types
        registered = tuple(get_registered_types())
    except Exception:
        registered = STEP_TYPES
    step_types = [{"name": t, "builtin": t in STEP_TYPES} for t in registered]

    roles = [
        {"name": n, "description": getattr(p, "description", "") or "", "role_type": getattr(p, "role_type", "") or ""}
        for n, p in sorted(_known_roles(cfg, source_dir).items())
        if p is not None
    ]

    tools: list[dict] = []
    try:
        try:
            import mini_agent.tools.builtin  # noqa: F401  确保内置工具已注册
        except Exception:
            pass
        from mini_agent.tools import get_default_registry
        reg = get_default_registry()
        for n in sorted(reg.names):
            td = reg.get(n)
            tools.append({
                "name": n,
                "description": (getattr(td, "description", "") or "")[:200],
                "group": getattr(td, "group", ""),
                "requires_approval": bool(getattr(td, "requires_approval", True)),
            })
    except Exception:
        pass

    skills: list[dict] = []
    try:
        from mini_agent.skills import SkillLoader
        dirs = []
        if getattr(cfg, "skills_dir", None):
            dirs.append(Path(cfg.skills_dir))
        if source_dir is not None:
            dirs.append(source_dir / "skills")
        for n, sk in sorted(SkillLoader(dirs)._all.items()):
            skills.append({"name": n, "description": getattr(sk, "description", "") or ""})
    except Exception:
        pass

    wf_names: list[str] = []
    try:
        wdir = store._dir
        wf_names = sorted(
            {p.stem for p in wdir.glob("*.yaml")}
            | {d.name for d in wdir.iterdir() if d.is_dir() and (d / "workflow.yaml").exists()}
        )
    except Exception:
        pass

    try:
        snippets = store.list_snippets()
    except Exception:
        snippets = []

    wf_cfg = getattr(cfg, "workflow", None)
    return {
        "step_types": step_types,
        "roles": roles,
        "tools": tools,
        "skills": skills,
        "workflows": wf_names,
        "snippets": snippets,
        "merge_strategies": ["concat_text", "json_array", "json_merge"],
        "modes": ["interactive", "autonomous"],
        "switches": {
            "visual_editor_enabled": bool(getattr(wf_cfg, "visual_editor_enabled", True)),
            "editor_backup_keep": int(getattr(wf_cfg, "editor_backup_keep", 20) or 20),
            "script_step_enabled": bool(getattr(wf_cfg, "script_step_enabled", False)),
            "python_step_enabled": bool(getattr(wf_cfg, "python_step_enabled", False)),
            "has_ruamel": has_ruamel(),
        },
    }
