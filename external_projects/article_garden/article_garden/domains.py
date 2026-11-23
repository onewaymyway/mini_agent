"""domains.py — 文章库（领域）注册表与 DomainContext。

* 一个文章库 = 一个完全独立的工作空间 data/domains/<id>/，互不共享数据（方案 §20）。
* 注册表 data/domains.json：{"version":1,"domains":[{id,name,template,status,created_at,last_activity}]}。
* 所有对库内文件的访问只能经 DomainContext.path()，并校验结果仍在该库根目录之内（防 `..` 越界与串库）。
* 归档只改状态、不删数据；彻底删除必须输入库名确认，且先导出 zip 备份。
"""

from __future__ import annotations

import re
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import charter as charter_mod
from . import paths, store, templates

DOMAIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
STATUS_ACTIVE, STATUS_ARCHIVED = "active", "archived"

# 方案 §4.1 的目录布局；DomainContext.ensure_layout() 创建。
LAYOUT_DIRS = (
    "library/imported", "library/generated",
    "wiki/digests", "wiki/concepts",
    "plan", "research/cache", "research/packs",
    "drafts", "runs", "logs",
)

_LOCK = threading.RLock()


class DomainError(ValueError):
    """文章库操作的可预期错误（id 非法/重复/不存在/确认不符…），信息直接展示给用户。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ 注册表

def _load_registry() -> Dict[str, Any]:
    try:
        data = store.read_json(paths.registry_path(), default=None)
    except ValueError as exc:  # 损坏时绝不当作“空”继续——否则后续写入会覆盖掉你的注册表
        raise DomainError(f"文章库注册表无法解析：{paths.registry_path()}（{str(exc)[:80]}）。"
                          "请手动修复或从备份恢复；各文章库目录里的数据没有受影响") from exc
    if not isinstance(data, dict) or not isinstance(data.get("domains"), list):
        return {"version": 1, "domains": []}
    return data


def _save_registry(reg: Dict[str, Any]) -> None:
    store.write_json_atomic(paths.registry_path(), reg)


def list_domains(include_archived: bool = False) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = [dict(d) for d in _load_registry()["domains"] if isinstance(d, dict) and d.get("id")]
    if not include_archived:
        rows = [d for d in rows if d.get("status", STATUS_ACTIVE) == STATUS_ACTIVE]
    return rows


def get_domain(domain_id: str) -> Dict[str, Any]:
    for d in list_domains(include_archived=True):
        if d["id"] == domain_id:
            return d
    raise DomainError(f"文章库不存在：{domain_id}")


def validate_domain_id(domain_id: str) -> str:
    did = (domain_id or "").strip()
    if not DOMAIN_ID_RE.match(did):
        raise DomainError("文章库 id 需以小写字母开头，只含小写字母/数字/下划线/连字符，长度 2–32，例如 ai、finance_cn")
    return did


def create_domain(domain_id: str, name: str, template: str = "blank",
                  copy_charter_from: Optional[str] = None) -> "DomainContext":
    """新建文章库：建目录、写章程。新库的自动化开关一律保守默认（settings.py），不继承任何其它库。"""
    did = validate_domain_id(domain_id)
    name = (name or "").strip()
    if not name:
        raise DomainError("请填写文章库名称")
    with _LOCK:
        reg = _load_registry()
        if any(d.get("id") == did for d in reg["domains"]):
            raise DomainError(f"文章库 id 已存在：{did}")
        if copy_charter_from:
            src = DomainContext(copy_charter_from)  # 不存在会抛 DomainError
            charter_raw, _ = charter_mod.load_charter(src.charter_path)
            tpl = f"copy:{copy_charter_from}"
        else:
            charter_raw = templates.template_charter(template)
            tpl = template
        root = paths.domains_root() / did
        if root.exists():
            raise DomainError(f"目录已存在但未登记：{root}（请先处理该目录）")
        ctx = DomainContext(did, _skip_registry_check=True)
        ctx.ensure_layout()
        charter_raw = dict(charter_raw)
        charter_raw["domain"] = charter_raw.get("domain") or name
        charter_mod.save_charter(ctx.charter_path, charter_raw)
        reg["domains"].append({"id": did, "name": name, "template": tpl, "status": STATUS_ACTIVE,
                               "created_at": _now(), "last_activity": _now()})
        _save_registry(reg)
    return ctx


def _update(domain_id: str, **changes: Any) -> None:
    with _LOCK:
        reg = _load_registry()
        for d in reg["domains"]:
            if d.get("id") == domain_id:
                d.update(changes)
                _save_registry(reg)
                return
    raise DomainError(f"文章库不存在：{domain_id}")


def rename_domain(domain_id: str, new_name: str) -> None:
    """只改显示名，id 与目录不变。"""
    new_name = (new_name or "").strip()
    if not new_name:
        raise DomainError("名称不能为空")
    _update(domain_id, name=new_name)


def archive_domain(domain_id: str) -> None:
    _update(domain_id, status=STATUS_ARCHIVED)


def restore_domain(domain_id: str) -> None:
    _update(domain_id, status=STATUS_ACTIVE)


def touch_activity(domain_id: str) -> None:
    try:
        _update(domain_id, last_activity=_now())
    except DomainError:
        pass


def delete_domain(domain_id: str, confirm_name: str, backup_dir: Optional[Path] = None) -> Path:
    """彻底删除：必须输入与登记完全一致的库名；先把整个库导出为 zip 备份，备份成功才删除。返回备份路径。"""
    info = get_domain(domain_id)
    if (confirm_name or "").strip() != info["name"]:
        raise DomainError("确认名称与文章库名称不一致，未删除")
    ctx = DomainContext(domain_id)
    backup = export_zip(domain_id, backup_dir or (paths.data_root() / "backups"))
    with _LOCK:
        if ctx.root.is_dir():
            shutil.rmtree(ctx.root)
        reg = _load_registry()
        reg["domains"] = [d for d in reg["domains"] if d.get("id") != domain_id]
        _save_registry(reg)
    return backup


def export_zip(domain_id: str, out_dir: Path) -> Path:
    """把整个文章库打成 zip（备份/迁移用）。"""
    ctx = DomainContext(domain_id)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = out_dir / f"{domain_id}_{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ctx.root.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(ctx.root.parent).as_posix())
    return target


# ------------------------------------------------------------------ DomainContext

class DomainContext:
    """某一个文章库的访问入口。页面与流水线代码只能通过它拿到路径，拿不到别的库的路径。"""

    def __init__(self, domain_id: str, _skip_registry_check: bool = False):
        self.id = validate_domain_id(domain_id)
        if not _skip_registry_check:
            get_domain(self.id)  # 未登记即抛 DomainError
        self.root = (paths.domains_root() / self.id).resolve()

    def __repr__(self) -> str:  # pragma: no cover
        return f"DomainContext({self.id!r})"

    def path(self, *parts: str) -> Path:
        """库内路径；结果必须仍在库根目录之内，否则抛 DomainError。"""
        p = self.root.joinpath(*parts).resolve()
        if p != self.root and self.root not in p.parents:
            raise DomainError(f"路径越出文章库范围：{'/'.join(parts)}")
        return p

    def ensure_layout(self) -> None:
        for d in LAYOUT_DIRS:
            self.path(d).mkdir(parents=True, exist_ok=True)

    # 常用路径（后续各期继续补充）
    @property
    def charter_path(self) -> Path:
        return self.path("charter.yaml")

    @property
    def settings_path(self) -> Path:
        return self.path("settings.yaml")

    @property
    def imported_dir(self) -> Path:
        return self.path("library", "imported")

    @property
    def generated_dir(self) -> Path:
        return self.path("library", "generated")

    @property
    def log_dir(self) -> Path:
        return self.path("logs")

    def load_charter(self) -> Tuple[Dict[str, Any], List[str]]:
        return charter_mod.load_charter(self.charter_path)

    def save_charter(self, charter: Dict[str, Any]) -> List[str]:
        warnings = charter_mod.save_charter(self.charter_path, charter)
        touch_activity(self.id)
        return warnings

    def info(self) -> Dict[str, Any]:
        return get_domain(self.id)

    def article_count(self) -> int:
        """库内文章数（P0 只数 Markdown 文件；P1 起改为读文章索引）。"""
        n = 0
        for d in (self.imported_dir, self.generated_dir):
            if d.is_dir():
                n += sum(1 for _ in d.rglob("*.md"))
        return n
