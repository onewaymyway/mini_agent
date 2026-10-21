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

## 角色文件格式（以系统校验为准）

每个角色一个文件 `{id}.md`。文件头以 `---` 开始、`---` 结束，中间**每行一个字段**，写成 `key: JSON值`，值必须在同一行。示例（字段值仅供参考）：

```
---
id: "char_001"
name: "李凌云"
role: "protagonist"
introduced_chapter: 1
identity: {"one_line_essence":"用强大武装自己的孤独者","age":17,"appearance":"身形单薄","signature_detail":"左手旧伤疤"}
psychology: {"core_desire":"证明自身价值","core_need":"被认可","core_fear":"被抛弃","wound":"幼年被父亲否定","belief":"只有强大才不会被抛弃","distorted_belief":"情感是弱点","moral_limit":"不伤害无辜","speech_pattern":"话少","stress_response":"沉默独自承担","decision_logic":"先保护身边人"}
behavior_guide: {"will_do":["无路可退时迸发意志力"],"wont_do":["无原则向敌人求饶"]}
abilities: {"current_level":"炼气第七层","special_skills":["剑法天赋异禀"],"weaknesses":["不擅团战"]}
arc: {"type":"成长弧","start_belief":"情感是弱点","end_belief":"接受自己的脆弱","cost":"放弃自我保护壳","current_stage":"扭曲信念期"}
state_log: [{"chapter":8,"status":"晋升内门弟子","key_change":"初次展现真实实力","emotional_state":"略有松动","relationship_changes":["与王道长：陌生→微妙"]}]
current_state: {"as_of_chapter":8,"location":"青云峰","physical":"轻伤已愈","emotional":"略感希望","known_secrets":["体质特殊"],"active_goals":["突破炼气期"]}
relationships: {"char_002":{"type":"师徒","planned_evolution":"陌生→微妙→信任→揭秘","current_actual":"微妙","tension":"低","last_updated_chapter":8}}
---
```

系统会校验下列规则，违反任何一条都会让本步骤失败重来：

1. 每行 `key: JSON值`；值是合法 JSON，**整个值写在同一行**，不要换行缩进；字段名不能重复。
2. JSON 字符串里需要引号时用中文「」或『』，**不要使用英文双引号**，否则解析失败。
3. `id` 只含字母、数字、下划线、连字符，且必须与文件名一致；`name` 必填，全书不得重名。
4. `identity / psychology / behavior_guide / abilities / arc / current_state / relationships` 必须是对象；`behavior_guide.will_do` 与 `wont_do` 必须是字符串数组；`introduced_chapter` 是正整数。
5. `state_log` 是数组，每条必须有正整数 `chapter`，**同一章只能有一条**：更新某章是替换该条，不是追加。
6. `relationships` 的键是对方角色 id，值必须是对象；`planned_evolution`（规划）与 `current_actual`（已写到的实际状态）分开写，不要混淆。
7. 文件头之后的正文由系统生成，不要手写；`角色索引.md` 也由系统生成。

---

## 核心操作：章后状态更新（对应流水线的「状态同步」步骤）

输入：本章概述（唯一的剧情依据）、`角色索引.md`、蓝图中本章的 `characters_present`。只改本章出场的角色文件，未出场的不要动。

1. **确定要更新的角色**：按「角色深度分级」只处理**主角和重要配角**；普通配角仅在本章对其有明确变化时更新；路人不更新。
2. **对每个要更新的角色，只改三处**：
   - `state_log`：把第 n 章的条目**替换**成本章内容（没有则新增）。每个字段一句话，保持精简，不要复述剧情。
   - `current_state`：更新为第 n 章结束时的状态，`as_of_chapter` 设为 n。
   - `relationships`：按概述更新本章发生变化的关系的 `current_actual` 与 `last_updated_chapter`；没变化的不要动。
3. **新角色**：概述中首次出现、值得建档的角色，新建文件，id 取 `char_` 加未被占用的三位编号，至少填 `id / name / role / introduced_chapter / current_state`。
4. **改完自检**：每个改过的文件，逐行确认符合上面的七条规则，尤其是同一行、无英文双引号、同一章只有一条。

不需要做的事：不要整体重写角色文件，不要写快照文件，不要改 `角色索引.md`，不要在本步骤里做一致性检查。

---

## 角色深度分级

| 类型 | 建档必填 | 章后更新 |
|------|------|------|
| 主角 | 全部字段 | 每章出场都更新 |
| 重要配角 | psychology + behavior_guide + relationships + current_state | 每章出场都更新 |
| 普通配角 | identity + psychology.speech_pattern + current_state（简化） | 仅当本章有明确变化 |
| 路人 | name + role + introduced_chapter | 不更新 |

---

## 本技能的其它用法（不在「状态同步」步骤里执行）

- **写作前查询**：写作所需的人物摘要由系统确定性地从 `角色索引.md` 与出场角色文件拼出，不需要在同步步骤里生成。
- **一致性警告**：行为是否违反 `wont_do`，由 consistency-checker 与完章核查负责，不在本步骤处理。

---

## 文件位置

- 主档案：`{WORKSPACE}/大纲规划/人物设定/角色/{id}.md`（每角色一个）
- 人物索引（系统生成）：`{WORKSPACE}/大纲规划/人物设定/角色索引.md`
- 人设卡（可读）：`{WORKSPACE}/大纲规划/人物设定/人设卡/{角色名}.md`
