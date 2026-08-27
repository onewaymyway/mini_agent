"""
external_projects/manifest.py — `project.yaml` schema 与解析

对应 `next_doc/external_projects_workspace_plan.md` §5.2 / 阶段 3 第一项。

设计要点（呼应原则一：引擎与宿主解耦）：
  - `ProjectManifest` 是 daemon 理解一个外部项目所需的**全部**信息，
    daemon 不应该、也不需要 import 外部项目自己的任何代码。
  - 校验只做"结构是否合法"（必填字段、类型、cron 表达式基本格式），
    不校验 `cmd` 指向的脚本是否存在/能否运行——那是 `registry.py`
    register 时的可选深校验，或者运行时才会暴露的问题，manifest 层
    只负责契约本身的正确性。
  - 没有引入新的第三方依赖：用标准库 + 项目已有的 `pyyaml` 依赖
    （`yaml.safe_load`），不用 pydantic——这份 schema 足够小，手写
    dataclass + 校验函数比引入模型框架更轻量，与 `Workspace`
    （`workspace.py`）保持同样的"数据类 + 显式校验"风格。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# cron 表达式最基本的形状校验：5 个空白分隔的字段。不做语义校验（比如
# 字段范围是否合法），语义留给真正触发调度时用的 cron 解析库去报错，这里
# 只挡住"完全不像 cron 表达式"的低级错误。
_CRON_FIELD_COUNT = 5


class ProjectManifestError(ValueError):
    """`project.yaml` 内容不合法（缺字段/类型错误/格式错误）。"""


class EntrypointParamError(ValueError):
    """触发 entrypoint 时传入的参数不合法（缺必填项/传了未声明的参数）。

    与 `ProjectManifestError` 分开是因为触发时机不同：这个错误发生在
    "运行时触发一次 entrypoint"，而不是"解析 project.yaml 本身"——
    `project.yaml` 的 `params` 声明本身完全合法，只是这一次调用传的
    值不满足声明（详见 `external_projects_kanban_integration_plan.md`
    阶段6）。
    """


@dataclass
class ParamSpec:
    """`project.yaml` 里 `entrypoints.<key>.params` 列表中的一条参数声明。

    只描述"看板/调用方需要收集什么"，不做任何类型转换——entrypoint 的
    `cmd` 本来就是一条 shell 命令，最终传参方式是"按声明顺序拼成位置
    参数追加在 `cmd` 后面"（`scheduler.py::_build_cmd_with_params()`），
    与 `run_stock_analysis.py` 这类现成脚本读 `sys.argv[1:]` 的既有
    写法直接对齐，不需要新的传参协议、也不需要脚本改造。
    """

    name: str
    required: bool = True
    default: Optional[str] = None
    help: str = ""


@dataclass
class EntrypointSpec:
    """`project.yaml` 里 `entrypoints.<key>` 对应的一条声明。"""

    key: str
    cmd: str
    schedule: Optional[str] = None
    timeout_sec: Optional[int] = None
    params: List[ParamSpec] = field(default_factory=list)

    @property
    def cron_expr(self) -> Optional[str]:
        """
        从 `schedule: "cron: 0 9,13 * * 1-5"` 里剥出纯 cron 表达式部分。

        约定沿用 §5.2 示例里的 `cron: <expr>` 前缀写法；`schedule` 为
        None，或不是 `cron:` 前缀（未来可能扩展 `interval:` 等），返回
        None，交给调用方决定是否是它能处理的调度形式。
        """
        if not self.schedule:
            return None
        prefix = "cron:"
        text = self.schedule.strip()
        if not text.lower().startswith(prefix):
            return None
        return text[len(prefix):].strip()


@dataclass
class HealthCheckSpec:
    cmd: str


@dataclass
class ResourceSpec:
    allowed_domains: List[str] = field(default_factory=list)
    max_concurrency: int = 1


@dataclass
class ReviewSpec:
    """`project.yaml` 里可选的 `review` 块——周期性"改进 review session"
    的调度声明。对应
    `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 4。

    刻意不放进 `entrypoints`：`entrypoints.schedule` 触发的是"跑既定
    代码的子进程"（无 LLM 参与），而 review session 是"daemon 发起一次
    真实 mini_agent 会话，读账本/积压账本/结果回溯，判断有没有值得
    处理的优化项"——语义完全不同，混在一起会让 `entrypoints` 的契约
    变得模糊（调用方无法再假设"entrypoints 里的每一项都是可以直接
    subprocess 执行的命令"）。
    """

    cadence: str = "weekly"
    enabled: bool = False


@dataclass
class KanbanStateSpec:
    """`kanban_view.states` 列表中的一项：一个状态列的取值与展示标签。

    对应 `external_projects_generic_kanban_view_refactor_plan.md` 第3/4节。
    """

    value: str
    label: str
    collapsed: bool = False


@dataclass
class KanbanMetricSpec:
    """`kanban_view.metric_fields` 列表中的一项：卡片正文展示的一个字段。"""

    field: str
    label: str
    format: str = "text"  # "number" | "percent" | "text"


@dataclass
class KanbanChangeStateSpec:
    """`kanban_view.change_state`：看板"变更状态"表单复用哪个 entrypoint。"""

    entrypoint: str
    id_param: str
    state_param: str
    note_param: Optional[str] = None


_KANBAN_METRIC_FORMATS = {"number", "percent", "text"}


@dataclass
class KanbanViewSpec:
    """`project.yaml` 里 `dashboard.kanban_view` 的完整声明。

    看板前端只认这份 schema 去动态画列/画卡片/画变更状态表单，不认任何
    具体项目名或字段名——详见
    `external_projects_generic_kanban_view_refactor_plan.md` 第2/3节。
    """

    data_file: str
    id_field: str
    title_field: str
    state_field: str
    states: List[KanbanStateSpec]
    metric_fields: List[KanbanMetricSpec] = field(default_factory=list)
    detail_list_field: Optional[str] = None
    change_state: Optional[KanbanChangeStateSpec] = None


@dataclass
class ProjectManifest:
    """一份已校验通过的 `project.yaml` 内容。"""

    name: str
    entrypoints: Dict[str, EntrypointSpec]
    health_check: Optional[HealthCheckSpec] = None
    resources: ResourceSpec = field(default_factory=ResourceSpec)
    review: ReviewSpec = field(default_factory=ReviewSpec)
    kanban_view: Optional[KanbanViewSpec] = None
    # manifest 所在目录，即该外部项目的 Workspace root。不是 project.yaml
    # 文件本身声明的字段，而是 load_manifest() 加载时按来源路径回填，方便
    # 调用方（registry/scheduler）不用另外再传一份 root。
    source_dir: Optional[Path] = None

    def entrypoint(self, key: str) -> EntrypointSpec:
        try:
            return self.entrypoints[key]
        except KeyError as exc:
            available = ", ".join(sorted(self.entrypoints)) or "(none)"
            raise ProjectManifestError(
                f"entrypoint '{key}' 未在 project.yaml 中声明，"
                f"可用: {available}"
            ) from exc

    def scheduled_entrypoints(self) -> List[EntrypointSpec]:
        """返回所有声明了 `schedule` 的 entrypoint（供调度器/阶段 3 使用）。"""
        return [ep for ep in self.entrypoints.values() if ep.schedule]


def build_cmd_with_params(entrypoint: EntrypointSpec, values: Optional[Dict[str, str]]) -> str:
    """把用户传入的参数值拼接到 `entrypoint.cmd` 后面，生成最终要执行的命令行。

    规则（对应 `external_projects_kanban_integration_plan.md` 阶段6）：
      - 没有声明 `params` 的 entrypoint：忽略传入的 `values`（不报错——
        兼容"看板/CLI 统一都传一个可能为空的 params 参数"的调用方式），
        原样返回 `cmd`。
      - 按 `entrypoint.params` 声明的顺序，依次取值：`values` 里有就用
        传入值；没有则看 `default`；`required=True` 且两者都没有 →
        抛 `EntrypointParamError`。`required=False` 且都没有 → 跳过
        （不追加这个位置参数，也不追加后面的参数——因为这是"位置参数"
        语义，跳过中间一个会让后面的参数错位；如果确实需要跳过某个
        可选参数、又要传后面的参数，应该在 project.yaml 里把该参数放
        在参数列表最后，或者改用 `default` 兜底）。
      - 值本身用 `shlex.quote()` 转义后再拼接，避免任何注入/被 shell
        重新解释（entrypoint 本来就是 `shell=True` 执行，这一步不可
        省略）。
      - 不接受 `values` 里出现声明之外的参数名——静默忽略容易让使用者
        以为传参生效了实际上没生效，明确报错更安全。
    """
    values = values or {}
    if not entrypoint.params:
        # 该 entrypoint 没有声明任何 params：视为"不支持传参"的 legacy
        # entrypoint，直接忽略调用方可能传来的 values（兼容"看板/CLI
        # 统一都传一个可能非空的 params 参数"的调用方式），不因为多传了
        # 键就报错——报错留给"声明了 params 但传了声明之外的键"这种更
        # 明确是使用者理解有误的情形（见下面的 unknown 检查）。
        return entrypoint.cmd

    unknown = set(values) - {p.name for p in entrypoint.params}
    if unknown:
        raise EntrypointParamError(
            f"entrypoint '{entrypoint.key}' 未声明参数: {', '.join(sorted(unknown))}"
        )

    import shlex

    parts = [entrypoint.cmd]
    for spec in entrypoint.params:
        raw_value = values.get(spec.name)
        if raw_value is None or raw_value == "":
            raw_value = spec.default
        if raw_value is None or raw_value == "":
            if spec.required:
                raise EntrypointParamError(
                    f"entrypoint '{entrypoint.key}' 缺少必填参数 '{spec.name}'"
                )
            break  # 位置参数语义：跳过一个可选参数后不再追加后续参数
        parts.append(shlex.quote(raw_value))
    return " ".join(parts)


def _require(data: Dict[str, Any], key: str, *, ctx: str) -> Any:
    if key not in data:
        raise ProjectManifestError(f"{ctx}: 缺少必填字段 '{key}'")
    return data[key]


def _require_str(data: Dict[str, Any], key: str, *, ctx: str) -> str:
    value = _require(data, key, ctx=ctx)
    if not isinstance(value, str) or not value.strip():
        raise ProjectManifestError(f"{ctx}: 字段 '{key}' 必须是非空字符串")
    return value


def _validate_cron_shape(expr: str, *, ctx: str) -> None:
    if len(expr.split()) != _CRON_FIELD_COUNT:
        raise ProjectManifestError(
            f"{ctx}: cron 表达式 '{expr}' 应为 {_CRON_FIELD_COUNT} 个"
            f"空白分隔字段（分 时 日 月 周），实际得到"
            f" {len(expr.split())} 个"
        )


def _parse_params(raw: Any, *, ctx: str) -> List[ParamSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProjectManifestError(f"{ctx}: 'params' 必须是一个列表")
    specs: List[ParamSpec] = []
    seen = set()
    for idx, item in enumerate(raw):
        item_ctx = f"{ctx}.params[{idx}]"
        if not isinstance(item, dict):
            raise ProjectManifestError(f"{item_ctx}: 必须是一个映射（name/required/default/help）")
        name = _require_str(item, "name", ctx=item_ctx)
        if name in seen:
            raise ProjectManifestError(f"{item_ctx}: 参数名 '{name}' 重复声明")
        seen.add(name)

        required = item.get("required", True)
        if not isinstance(required, bool):
            raise ProjectManifestError(f"{item_ctx}: 'required' 必须是布尔值")

        default = item.get("default")
        if default is not None and not isinstance(default, str):
            raise ProjectManifestError(f"{item_ctx}: 'default' 必须是字符串")
        # 有默认值的参数即使 required 没显式写 false，语义上也应该允许
        # 不传——但这里不强行覆盖用户写的 required，只是把这条约束留给
        # 调用方（scheduler.py）：required=True 且 default 非空时，缺省
        # 传参会用 default 兜底而不是报错，见该模块的 `_build_cmd_with_params()`。

        help_text = item.get("help", "")
        if not isinstance(help_text, str):
            raise ProjectManifestError(f"{item_ctx}: 'help' 必须是字符串")

        specs.append(ParamSpec(name=name, required=required, default=default, help=help_text))
    return specs


def _parse_entrypoint(key: str, raw: Any) -> EntrypointSpec:
    ctx = f"entrypoints.{key}"
    if not isinstance(raw, dict):
        raise ProjectManifestError(f"{ctx}: 必须是一个映射（cmd/schedule/timeout_sec/params）")
    cmd = _require_str(raw, "cmd", ctx=ctx)

    schedule = raw.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, str) or not schedule.strip():
            raise ProjectManifestError(f"{ctx}: 'schedule' 必须是非空字符串")
        schedule = schedule.strip()
        if schedule.lower().startswith("cron:"):
            _validate_cron_shape(schedule[len("cron:"):].strip(), ctx=ctx)

    timeout_sec = raw.get("timeout_sec")
    if timeout_sec is not None and (
        not isinstance(timeout_sec, int) or isinstance(timeout_sec, bool) or timeout_sec <= 0
    ):
        raise ProjectManifestError(f"{ctx}: 'timeout_sec' 必须是正整数")

    params = _parse_params(raw.get("params"), ctx=ctx)

    return EntrypointSpec(
        key=key, cmd=cmd, schedule=schedule, timeout_sec=timeout_sec, params=params
    )


def _parse_health_check(raw: Any) -> Optional[HealthCheckSpec]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProjectManifestError("health_check: 必须是一个映射（cmd）")
    cmd = _require_str(raw, "cmd", ctx="health_check")
    return HealthCheckSpec(cmd=cmd)


def _parse_resources(raw: Any) -> ResourceSpec:
    if raw is None:
        return ResourceSpec()
    if not isinstance(raw, dict):
        raise ProjectManifestError("resources: 必须是一个映射（allowed_domains/max_concurrency）")

    allowed_domains = raw.get("allowed_domains", [])
    if not isinstance(allowed_domains, list) or not all(
        isinstance(d, str) for d in allowed_domains
    ):
        raise ProjectManifestError("resources.allowed_domains: 必须是字符串列表")

    max_concurrency = raw.get("max_concurrency", 1)
    if (
        not isinstance(max_concurrency, int)
        or isinstance(max_concurrency, bool)
        or max_concurrency < 1
    ):
        raise ProjectManifestError("resources.max_concurrency: 必须是 >= 1 的整数")

    return ResourceSpec(allowed_domains=list(allowed_domains), max_concurrency=max_concurrency)


def _parse_review(raw: Any) -> ReviewSpec:
    if raw is None:
        return ReviewSpec()
    if not isinstance(raw, dict):
        raise ProjectManifestError("review: 必须是一个映射（cadence/enabled）")

    cadence = raw.get("cadence", "weekly")
    if not isinstance(cadence, str) or not cadence.strip():
        raise ProjectManifestError("review.cadence: 必须是非空字符串")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ProjectManifestError("review.enabled: 必须是布尔值")

    return ReviewSpec(cadence=cadence.strip(), enabled=enabled)


def _parse_kanban_states(raw: Any, *, ctx: str) -> List[KanbanStateSpec]:
    if not isinstance(raw, list) or not raw:
        raise ProjectManifestError(f"{ctx}: 'states' 必须是一个非空列表")
    specs: List[KanbanStateSpec] = []
    seen = set()
    for idx, item in enumerate(raw):
        item_ctx = f"{ctx}.states[{idx}]"
        if not isinstance(item, dict):
            raise ProjectManifestError(f"{item_ctx}: 必须是一个映射（value/label/collapsed）")
        value = _require_str(item, "value", ctx=item_ctx)
        if value in seen:
            raise ProjectManifestError(f"{item_ctx}: 状态取值 '{value}' 重复声明")
        seen.add(value)
        label = _require_str(item, "label", ctx=item_ctx)
        collapsed = item.get("collapsed", False)
        if not isinstance(collapsed, bool):
            raise ProjectManifestError(f"{item_ctx}: 'collapsed' 必须是布尔值")
        specs.append(KanbanStateSpec(value=value, label=label, collapsed=collapsed))
    return specs


def _parse_kanban_metric_fields(raw: Any, *, ctx: str) -> List[KanbanMetricSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProjectManifestError(f"{ctx}: 'metric_fields' 必须是一个列表")
    specs: List[KanbanMetricSpec] = []
    for idx, item in enumerate(raw):
        item_ctx = f"{ctx}.metric_fields[{idx}]"
        if not isinstance(item, dict):
            raise ProjectManifestError(f"{item_ctx}: 必须是一个映射（field/label/format）")
        field_name = _require_str(item, "field", ctx=item_ctx)
        label = _require_str(item, "label", ctx=item_ctx)
        fmt = item.get("format", "text")
        if fmt not in _KANBAN_METRIC_FORMATS:
            raise ProjectManifestError(
                f"{item_ctx}: 'format' 必须是 {sorted(_KANBAN_METRIC_FORMATS)} 之一，"
                f"实际得到 {fmt!r}"
            )
        specs.append(KanbanMetricSpec(field=field_name, label=label, format=fmt))
    return specs


def _parse_kanban_change_state(raw: Any, *, ctx: str) -> Optional[KanbanChangeStateSpec]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProjectManifestError(f"{ctx}: 必须是一个映射（entrypoint/id_param/state_param/note_param）")
    entrypoint = _require_str(raw, "entrypoint", ctx=ctx)
    id_param = _require_str(raw, "id_param", ctx=ctx)
    state_param = _require_str(raw, "state_param", ctx=ctx)
    note_param = raw.get("note_param")
    if note_param is not None and not isinstance(note_param, str):
        raise ProjectManifestError(f"{ctx}: 'note_param' 必须是字符串")
    return KanbanChangeStateSpec(
        entrypoint=entrypoint, id_param=id_param, state_param=state_param, note_param=note_param
    )


def _parse_kanban_view(
    raw: Any, *, entrypoints: Dict[str, EntrypointSpec]
) -> Optional[KanbanViewSpec]:
    """解析 `dashboard.kanban_view`。必须在 `entrypoints` 解析完成之后调用，
    因为 `change_state.entrypoint` 是跨字段的引用完整性检查（第4节）。
    """
    if raw is None:
        return None
    ctx = "dashboard.kanban_view"
    if not isinstance(raw, dict):
        raise ProjectManifestError(f"{ctx}: 必须是一个映射")

    data_file = _require_str(raw, "data_file", ctx=ctx)
    id_field = _require_str(raw, "id_field", ctx=ctx)
    title_field = _require_str(raw, "title_field", ctx=ctx)
    state_field = _require_str(raw, "state_field", ctx=ctx)
    states = _parse_kanban_states(raw.get("states"), ctx=ctx)
    metric_fields = _parse_kanban_metric_fields(raw.get("metric_fields"), ctx=ctx)

    detail_list_field = raw.get("detail_list_field")
    if detail_list_field is not None and not isinstance(detail_list_field, str):
        raise ProjectManifestError(f"{ctx}: 'detail_list_field' 必须是字符串")

    change_state = _parse_kanban_change_state(raw.get("change_state"), ctx=f"{ctx}.change_state")
    if change_state is not None and change_state.entrypoint not in entrypoints:
        available = ", ".join(sorted(entrypoints)) or "(none)"
        raise ProjectManifestError(
            f"{ctx}.change_state: entrypoint '{change_state.entrypoint}' "
            f"未在 project.yaml 的 entrypoints 中声明，可用: {available}"
        )

    return KanbanViewSpec(
        data_file=data_file,
        id_field=id_field,
        title_field=title_field,
        state_field=state_field,
        states=states,
        metric_fields=metric_fields,
        detail_list_field=detail_list_field,
        change_state=change_state,
    )


def parse_manifest(text: str, *, source_dir: Optional[Path] = None) -> ProjectManifest:
    """从 `project.yaml` 的文本内容解析出 `ProjectManifest`，做结构校验。"""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProjectManifestError(f"project.yaml 不是合法的 YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ProjectManifestError("project.yaml 顶层必须是一个映射")

    name = _require_str(data, "name", ctx="project.yaml")

    raw_entrypoints = _require(data, "entrypoints", ctx="project.yaml")
    if not isinstance(raw_entrypoints, dict) or not raw_entrypoints:
        raise ProjectManifestError("project.yaml: 'entrypoints' 必须是至少含一项的映射")
    entrypoints = {
        key: _parse_entrypoint(key, raw) for key, raw in raw_entrypoints.items()
    }

    health_check = _parse_health_check(data.get("health_check"))
    resources = _parse_resources(data.get("resources"))
    review = _parse_review(data.get("review"))
    dashboard = data.get("dashboard")
    if dashboard is not None and not isinstance(dashboard, dict):
        raise ProjectManifestError("project.yaml: 'dashboard' 必须是一个映射")
    kanban_view = _parse_kanban_view(
        (dashboard or {}).get("kanban_view"), entrypoints=entrypoints
    )

    return ProjectManifest(
        name=name,
        entrypoints=entrypoints,
        health_check=health_check,
        resources=resources,
        review=review,
        kanban_view=kanban_view,
        source_dir=Path(source_dir).expanduser().resolve() if source_dir else None,
    )


def load_manifest(path: Path) -> ProjectManifest:
    """
    从磁盘加载一份 `project.yaml`。

    `path` 既可以是 `project.yaml` 文件本身，也可以是外部项目的根目录
    （此时约定读取 `<path>/project.yaml`），方便调用方直接传 `Workspace.root`
    或 `Workspace.project_yaml_path`。
    """
    path = Path(path).expanduser()
    manifest_path = path / "project.yaml" if path.is_dir() else path
    if not manifest_path.exists():
        raise ProjectManifestError(f"project.yaml 不存在: {manifest_path}")
    text = manifest_path.read_text(encoding="utf-8")
    return parse_manifest(text, source_dir=manifest_path.parent)
