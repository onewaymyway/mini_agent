# 完整示例

## 示例 1：单文件小型 skill

```markdown
---
name: fastapi-conventions
description: FastAPI 项目的代码规范与最佳实践，包括路由组织、依赖注入、错误处理。当用户编写或审查 FastAPI 代码、路由、Pydantic 模型时使用。
triggers: fastapi, pydantic, 路由, dependency injection, api endpoint
---

# FastAPI Conventions

## 路由组织
- 按资源拆分 `APIRouter`，统一在 `app/api/v1/__init__.py` 汇总注册
- 路径统一使用复数名词：`/users`, `/orders`

## 依赖注入
- 数据库 session 通过 `Depends(get_db)` 注入，不要在路由函数里直接 `SessionLocal()`
- 鉴权统一用 `Depends(get_current_user)`

## 错误处理
- 业务异常抛 `HTTPException`，统一在 `app/core/exceptions.py` 定义错误码常量
```

## 示例 2：分层 skill（带 resources）

目录：

```
.claude/skills/db-migration/
├── SKILL.md
└── references/
    ├── rollback.md
    └── zero-downtime.md
```

`SKILL.md`：

```markdown
---
name: db-migration
description: 数据库迁移脚本的编写规范与安全检查。当用户编写、审查数据库 migration 脚本时使用。
triggers: migration, 数据库迁移, alembic, schema变更
resources:
  - id: rollback
    path: references/rollback.md
    description: 迁移回滚策略与常见回滚失败场景
    triggers: 回滚, rollback, 迁移失败
  - id: zero-downtime
    path: references/zero-downtime.md
    description: 零停机迁移的分阶段执行方案（加列/建索引/删列的顺序要求）
    triggers: 零停机, 不停机, 大表迁移, 线上迁移
---

# DB Migration

## 核心规范（永远注入）
- 每个 migration 必须可逆，禁止在 up() 里做不可逆的破坏性操作而不写 down()
- 大表加索引必须用 `CONCURRENTLY`（Postgres）避免锁表
- migration 文件命名：`YYYYMMDDHHMM_verb_noun.py`

## 需要更详细方案时
- 涉及回滚 → 加载 `rollback` 子资源
- 线上大表、要求不停机 → 加载 `zero-downtime` 子资源
```

## 示例 3：带 browse_paths 的大型文档库 skill

```markdown
---
name: internal-sdk-docs
description: 公司内部 SDK 的用法索引。当用户询问内部 SDK 具体 API 用法时使用。
triggers: 内部sdk, internal sdk
browse_paths:
  - path: references/api-reference/
    description: 按模块拆分的完整 API 参考（几十个文件），请用 grep 按函数名/类名检索，不要整份读取
  - path: references/changelog/
    description: 各版本变更记录，按版本号查找具体文件
---

# Internal SDK Docs

## 使用方式
本 skill 主要提供索引说明，具体 API 细节体量太大不适合整段注入。

1. 先确认用户问的是哪个模块/函数
2. 用 `grep -rn "函数名" references/api-reference/` 定位到具体文件
3. 只 `view` 命中的那个文件，不要遍历整个目录

## 常见模块速查
- 鉴权相关 → `references/api-reference/auth/`
- 消息队列 → `references/api-reference/mq/`
```

这个例子里完全没有用 `resources`，因为内容是"库"，agent 该自己去检索定位，
而不是被整段塞进 context。
