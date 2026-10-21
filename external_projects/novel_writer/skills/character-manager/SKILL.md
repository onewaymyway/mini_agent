---
name: character-manager
description: 小说人物管理技能。负责创建和维护所有角色的完整档案，追踪角色状态变化、人物关系网络、成长轨迹和动机演变。当需要新增角色、查询角色信息、更新角色状态、分析角色关系、检查角色行为是否符合人物性格时，必须使用此技能。触发词：创建角色、人物档案、角色关系、角色设定、人物性格、角色状态更新、角色一致性。
---

# 人物管理技能（Character Manager）

## 存储约定（第四阶段）

每个角色一个文件 `大纲规划/人物设定/角色/{id}.md`：文件头用 `key: JSON值` 逐行保存下文的字段（每个值写在同一行），正文由系统生成。`角色索引.md` 由系统生成，不要手写。章后同步只修改本章出场角色的文件，新角色新建文件；不要整体重写全部角色。行为边界直接写在 `behavior_guide` 字段中，不再单独生成 behavior_index。

## 职责

建立和维护所有角色的"活档案"。与 character-designer 分工：designer 负责创作阶段，本技能负责写作中的状态追踪管理。

---

## 从 character-designer 建档：字段映射

```
人设卡字段                → 角色文件字段
一句话本质                → identity.one_line_essence
核心欲望（表层/深层）     → psychology.core_desire / core_need
核心恐惧                  → psychology.core_fear
塑造性伤口                → psychology.wound
核心信念/扭曲信念         → psychology.belief / distorted_belief
道德底线                  → psychology.moral_limit
标志性外形细节            → identity.signature_detail
说话方式特征              → psychology.speech_pattern
压力应激反应              → psychology.stress_response
决策倾向                  → psychology.decision_logic
"他会做"列表              → behavior_guide.will_do
"他不会做"列表            → behavior_guide.wont_do （一致性校验核心依据）
弧线类型/成长代价         → arc.type / arc.cost
```

---

## 角色档案结构（角色文件）

```json
{
  "char_001": {
    "id": "char_001",
    "name": "李凌云",
    "role": "protagonist",
    "introduced_chapter": 1,
    "identity": {
      "one_line_essence": "用强大武装自己的孤独者",
      "age": 17,
      "appearance": "身形单薄，眼神坚毅，左手有道旧伤疤",
      "signature_detail": "左手旧伤疤——情绪激动时无意识握紧"
    },
    "psychology": {
      "core_desire": "证明自身价值",
      "core_need": "被认可，不再孤独",
      "core_fear": "被所有人抛弃，成为无用之人",
      "wound": "幼年父亲当众否定天赋",
      "belief": "只有足够强大才能不被抛弃",
      "distorted_belief": "情感是弱点，不能依赖任何人",
      "moral_limit": "不会主动伤害无辜，不会出卖同伴",
      "speech_pattern": "话少，言简意赅，激动时才多话",
      "stress_response": "沉默，独自承担，拒绝求助",
      "decision_logic": "优先保护身边人，其次自身利益"
    },
    "behavior_guide": {
      "will_do": ["无路可退时迸发惊人意志力", "用行动而非言语回报善意"],
      "wont_do": ["无原则向敌人求饶", "公开场合表达脆弱", "背刺信任的人"]
    },
    "abilities": {
      "current_level": "炼气第七层",
      "special_skills": ["剑法天赋异禀", "混沌根骨（隐藏）"],
      "weaknesses": ["内力底子薄", "不擅长团战"]
    },
    "arc": {
      "type": "成长弧",
      "start_belief": "情感是弱点",
      "end_belief": "接受自己的脆弱",
      "cost": "放弃孤独者的自我保护壳",
      "current_stage": "扭曲信念期，未触发成长转折"
    },
    "state_log": [
      {
        "chapter": 8,
        "status": "晋升内门弟子",
        "key_change": "初次展现真实实力",
        "emotional_state": "初露锋芒，内心略有松动",
        "relationship_changes": ["与王道长：陌生→微妙"]
      }
    ],
    "current_state": {
      "as_of_chapter": 8,
      "location": "青云峰",
      "physical": "轻伤已愈",
      "emotional": "略感希望，仍保持警惕",
      "known_secrets": ["体质特殊", "师傅曾认识其父"],
      "active_goals": ["突破炼气期", "调查父亲失踪"]
    },
    "relationships": {
      "char_002": {
        "type": "师徒",
        "planned_evolution": "陌生→微妙→信任→揭秘",
        "current_actual": "微妙——发现师傅认识父亲，开始起疑",
        "tension": "低",
        "last_updated_chapter": 8
      }
    }
  }
}
```

**关键设计**：relationships 分离 planned_evolution（规划）和 current_actual（已写到的实际状态），避免混淆将来计划和当前事实。

---

## 核心操作

### 写作前查询（chapter-writer 调用）

返回"写作提示卡"：

```
角色：李凌云
当前状态：内门弟子 / 青云峰 / 轻伤已愈
当前情绪：略感希望，仍警惕
说话方式：话少，言简意赅，激动时才多话
行为边界：
  可以：沉默承担压力 / 用行动回报善意
  不会：无原则求饶 / 公开表露脆弱
当前关系（本章出场）：
  王道长——微妙，起疑中
已知信息：体质特殊 / 师傅认识父亲
未知信息（不能表现出知道）：身世全貌
```

### 章后状态更新

```
章节: n
角色: char_001
变化类型: [状态/情绪/关系/能力/信息/弧线]
变化内容: 具体描述
触发原因: 什么事件导致
```

### 一致性警告（consistency-checker 触发）

```
⚠️ 行为一致性警告
角色：李凌云
第n章描述：主动向敌人求饶
wont_do 记录：不会无原则求饶
修改建议：改为迂回策略，或补充保护他人的前提
```

---

## 角色深度分级

| 类型 | 必填 |
|------|------|
| 主角 | 全部字段 |
| 重要配角 | psychology + behavior_guide + relationships + current_state |
| 普通配角 | identity + speech_pattern + current_state（简化）|
| 路人 | name + role + 首次登场章节 |

---

## 文件管理

所有文件保存到工作目录（{WORKSPACE}）：

- 主档案：`{WORKSPACE}/大纲规划/人物设定/角色/（每角色一个 {id}.md）`
- 状态快照：`{WORKSPACE}/大纲规划/人物设定/snapshots/ch_{n}.json`（每章结束时存储）
- 人设卡（可读）：`{WORKSPACE}/大纲规划/人物设定/人设卡/{角色名}.md`
