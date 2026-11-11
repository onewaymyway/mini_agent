# 外部项目注册：扫描 external_projects/ 目录选择注册（实施记录）

## 需求
看板「🗂️ 外部项目」→「➕ 注册新项目」除手填路径外，增加“扫描 `external_projects/` 目录，列出还未注册的目录，通过选择来添加”。

## 实现
- `external_projects/registry.py::scan_unregistered(scan_root, registry)`：只读；扫描一级子目录，已注册按**路径**判断（注册名与目录名不同也能识别）；跳过隐藏/`_`开头/`__pycache__`/非目录；每个候选带 `has_manifest/manifest_ok/manifest_error/suggested_name/name_conflict/registrable`。manifest 合法时建议名取 `manifest.name`。
- `GET /v1/external_projects/candidates`（owner）：扫描根固定为 daemon 工作目录下的 `external_projects/`（与 register 记录 `main_project_root` 的默认取值一致），不接受调用方传目录，避免变成任意目录探测。
- `client.external_project_candidates()`；`app.py::_ext_scan_candidates_section`（`@st.fragment`，遵循看板规范）：点扫描后列出候选，可改名并“注册这个目录”；缺/坏 `project.yaml` 的项只提示原因、无注册按钮；重名给警告。注册沿用既有 register 接口——**默认校验 manifest、默认不开启自动调度**（opt-in 不变）。注册成功后清掉扫描结果并刷新。
- 手填路径表单保留（可注册 `external_projects/` 之外的目录）。

## 取舍
- 不做“自动注册”：扫描只列出，必须用户逐个选择；扫描本身不写任何文件。
- 候选里“无 project.yaml”的目录也列出（解释为什么不能注册），而不是静默隐藏。
- 扫描是按钮触发而非每次渲染都扫，避免每次 rerun 多一次请求。

## 验证
`tests/test_external_projects_scan_candidates.py`（12 用例）：纯函数（缺目录、已注册/隐藏/文件过滤、改名后按路径识别、坏 manifest、建议名与重名、只读）、路由（列出→注册→不再出现、目录不存在）、看板 UI（未扫描不列出、扫描后列出且不可注册项无按钮、改名后注册调用参数正确并清扫描结果、全部已注册/目录缺失/扫描失败提示）。与既有 external_projects 相关 122 个用例一并通过。
未做：真实 Streamlit 页面截图与真实 daemon 联调（沙箱内只有 AppTest 与最小 FastAPI app）。
