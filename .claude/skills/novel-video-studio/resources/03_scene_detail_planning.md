# 阶段3：单大场景详细规划 (micro_scene)

**每次只处理 `macro_scenes.yaml` 里的一个大场景**（按 `status: pending`
挑一条，通常按顺序来），这样每个大场景可以独立重跑、断点续跑。

**依赖**：无外部 API，纯 Agent 推理 + 文件读写；引用缺失时会回调阶段1
模式B、阶段4，这两个是外部调用，不是本阶段自己实现。
**校验脚本**：`scripts/check_scene_detail.py`。

产物路径（以处理 `macro_01` 为例）：
```
novel_output/小说名_20260911/
├── macro_scenes.yaml       # 本阶段处理完把对应条目 status 从 pending 改成 planned
├── global/                 # 可能被本阶段追加更新（回补角色/地点）
└── macro_scene_01/         # 本阶段新建
    └── scene_detail.yaml
```

## Step 1：取出待处理的大场景

从 `macro_scenes.yaml` 挑一条 `status: pending` 的记录（通常按 id 顺序，
用户也可以指定 `macro_id`），读取 `raw_text`/`uses_characters`/
`uses_locations`。新建工作目录 `<output_dir>/macro_scene_<id后缀>/`。

## Step 2：切分小场景（micro_scene）

把该大场景的 `raw_text` 按镜头/画面切换点切成若干小场景，**每个小场景
对应最终一支独立生成的视频 clip**，口播时长控制在 **4-12 秒**。

## Step 3：区分旁白/对话，写 content_blocks

每个小场景内部，按原文顺序拆成 `content_blocks` 列表：

```yaml
content_blocks:
  - type: narration
    text: "夜色渐浓，林然推门走进客栈"   # 旁白，可以改写成适合口播的转述
    speaker: null
  - type: dialogue
    text: "客官打尖还是住店？"           # 必须原文逐字摘录，不允许改写/补写
    speaker: char_02                       # 说这句话的角色 id
```

`speaker`：`narration` 恒为 `null`；`dialogue` 必须填 `uses_characters`
里的角色 id（说话人不明确时结合上下文判断，实在无法判断宁可处理成
`narration`（转述"有人说……"）也不要瞎归属）。

**对话严禁改写或补写**——哪怕原文对话很简短或语气不适合直接口播，也
只能摘录原文，不能"优化"，校验脚本会做子串匹配，改写过的文本会被判定
为疑似臆造直接打回。

### `dialogue` 只能放"角色说出口的话本身"，不能混入动作/神态描写

这是最容易做错的一步，务必按下面的规则拆：

- `dialogue.text` 必须**只**是引号（「」/""/『』）里角色实际说的话，
  一个字都不能多；原文里夹在引号之间的"XX说""XX一边擦着桌子一边说"
  这类描述说话人动作/神态的句子，**必须单独拆成一个 `narration`
  block**，不能和前后的引号内容拼在一起塞进同一个 `dialogue` block；
- 如果一句引号被中间的动作描写打断成两段（原文写作
  `"前半句，"她一边擦桌子一边说，"后半句。"`），要拆成三个
  content_blocks：`dialogue`（前半句）→ `narration`（动作描写）→
  `dialogue`（后半句），**不能**合并成一个 `dialogue` block 把动作也
  包含进去；
- 凡是"她告诉他……""他表示……""对方解释道……"这类**间接转述**（原文
  本身没有用引号直接引用话语，只是叙事者转述大意），整段都算
  `narration`，不能标成 `dialogue`（哪怕转述的内容看起来像是在说话）；
- 校验脚本 `check_scene_detail.py` 会做两道检查兜底：① 原文里能抠出
  引号片段时，`dialogue.text` 必须落在某个引号片段内部；② `dialogue.text`
  里出现"一边""说道""苦笑""摇头""脸色""告诉"等动作/神态提示词会直接
  报错。**这两道检查不通过就说明拆分方式不对，需要回来重新拆，不是
  校验脚本太严格**。

**错误示例**（动作和转述混进了 dialogue，会被校验脚本打回）：
```yaml
content_blocks:
  - type: dialogue
    text: "又是三年没个音信，沈婉一边擦着桌子一边说，江湖上都传你死在关外了。"
    speaker: char_02
  - type: dialogue
    text: "沈婉脸色微变，她告诉林然，最近镇上确实不太平，入夜后总有黑影出没在将军府附近。"
    speaker: char_02
```

**正确拆法**（原文：`"又是三年没个音信，"沈婉一边擦着桌子一边说，"江湖上都传你死在关外了。"……沈婉说："镇上最近不太平，入夜后总有黑影出没在将军府附近，镇民都不敢靠近。"`）：
```yaml
content_blocks:
  - type: dialogue
    text: "又是三年没个音信，"
    speaker: char_02
  - type: narration
    text: "沈婉一边擦着桌子一边说，"
    speaker: null
  - type: dialogue
    text: "江湖上都传你死在关外了。"
    speaker: char_02
  - type: narration
    text: "林然苦笑着摇头，在窗边坐下，望着窗外渐暗的天色。"
    speaker: null
  - type: narration
    text: "沈婉说："
    speaker: null
  - type: dialogue
    text: "镇上最近不太平，入夜后总有黑影出没在将军府附近，镇民都不敢靠近。"
    speaker: char_02
```

拆细之后 `content_blocks` 数量会变多，这是预期的——`novel-asset-generator`
按每个 block 分别配音（旁白一个音色、对话按角色音色），拆细才能保证
"角色说话的部分"和"动作/神态描写的旁白部分"用各自正确的音色朗读，也
让最终字幕（对话用「」包裹）只包裹真正说出口的话，不会把动作描写也
包在引号里显示。

## Step 4：标注引用与镜头描述

```yaml
micro_scenes:
  - id: micro_01
    macro_id: macro_01
    uses_characters: [char_01, char_02]
    uses_locations: [loc_01]
    visual_hint: "客栈门口，夜晚，林然推门而入，暖光从门内透出"
    content_blocks: [...]
    duration_sec: null       # 本阶段不填，等阶段4跑完TTS回填
    status: pending           # 阶段5处理完回写 done/failed
```

## Step 5：引用完整性校验与回补循环

检查每条 `uses_characters`/`uses_locations`（以及每条 `dialogue.speaker`）
是否都能在全局库里找到，**且已有 `asset_path`**：

- 引用了全局库里完全不存在的角色/地点：
  1. 调用阶段1模式B，把本大场景的 `raw_text` 交给它补抽取，拿到新增
     角色/地点 id；
  2. 调用阶段4（只处理这几个新增角色/地点）补生成定妆图，回填
     `asset_path`；
  3. 回到 Step 2-4，重新调整涉及这些新增实体的小场景规划；
- 角色/地点 id 已存在但 `asset_path` 还是 `null`：只需调用阶段4补生成，
  不需要重新调用阶段1。

这个回补循环**必须在本阶段内部闭环完成**，不能把"引用了不存在的资源"
遗留到阶段4/5才发现。

## Step 6：写入产物前的自查清单（Agent 语义自查，脚本不做这件事）

脚本对"dialogue 是否混入了动作/神态描写"这件事**只能做启发式提示，
不能做可靠判定**——某些看起来像动作描写的字（"告诉""看着""沉默"……）
完全可能就是角色台词本身要说的内容，而不是叙事者对说话动作的描述，
机械关键词匹配区分不出这两种情况。所以在跑脚本之前，先对每个新写的
`dialogue` block 过一遍下面这份自查清单（这是 Agent 该做语义判断的
地方，不要指望脚本帮你判断对错）：

1. 这句话如果去掉，是不是"角色说的内容"就不完整了？—— 是，才应该留在
   dialogue 里；如果去掉的是"谁在做什么表情/动作说的这句话"，那部分
   要拆到 narration。
2. 读起来像是叙事者的视角在描述说话人（"她一边...一边说""他苦笑着
   摇了摇头"），还是角色自己嘴里说出的话？前者是 narration，后者才是
   dialogue。
3. 如果这句 dialogue 被脚本标了 warning（见下），先按上面两条自查，
   确认后再决定要不要挪，**不要看到 warning 就无脑删字或强行改写**——
   改动台词内容本身也违反"对话严禁改写"的规则。

## Step 7：写入产物 + 跑校验

写入 `<output_dir>/macro_scene_<id后缀>/scene_detail.yaml`，然后：

```bash
python .claude/skills/novel-video-studio/scripts/check_scene_detail.py \
  <output_dir> <macro_id>
```

**errors 非空 → 必须回 Step 2-5 调整，重新跑校验，直到 errors 清空**
（子串反臆造、speaker 归属、引用完整性、id 唯一性这几类是客观事实
校验，没有"人工判断后决定不改"的空间）。

**warnings 非空但 errors 为空 → 允许通过**，但不能直接忽略：warnings
里目前主要是两类需要人工/Agent 复核而非机械修正的信号：
- "疑似动作/神态描写用词"：按上面 Step 6 的自查清单判断是否要挪到
  narration，判断后确认不需要改的，可以直接放行，不用为了消除 warning
  而强行改写台词；
- "原文直引号数量为奇数"：说明该大场景原文里直引号的全局奇偶配对
  可能已经错位（比如原文里引用了书名/术语用了单个 `"` 而不是成对
  出现），需要人工看一眼这个大场景的 raw_text，确认引号提取没有把
  某句台词的边界切错，必要时手动核对相关 dialogue block 是否准确。

通过后（errors 为空），把 `macro_scenes.yaml` 对应条目的 `status` 从
`pending` 改成 `planned`。

## 校验脚本说明 `check_scene_detail.py`

**errors（客观事实校验，必须清零才能通过）：**

1. 每个 `micro_scene` 的引用是否都能在全局库找到，且 `asset_path` 非空；
2. 每个 `dialogue` 的 `speaker` 是否在该 `micro_scene` 的
   `uses_characters` 列表里；
3. **对话真实性校验**：每个 `dialogue.text`（去空白标点后）是否能在该
   大场景 `raw_text`（同样去空白标点）里找到子串匹配，找不到判定为
   疑似臆造对话，报错；**进一步**，若原文里能提取出引号片段，
   `dialogue.text` 还必须落在某个引号片段内部（不能是"引号内容+引号
   外动作/转述"整句糅合）；
4. 每个小场景至少有一个非空 `content_blocks`；
5. `micro_scenes` 的 `id` 在整个项目范围内（不只是本大场景）不重复。

**warnings（启发式提示，不阻断，需要 Agent 结合上下文判断）：**

6. `dialogue.text` 是否包含"一边"/"苦笑"/"脸色"/"告诉"等动作神态类
   提示词——**这只是线索不是判决**：这些字也完全可能就是角色台词本身
   的内容（例如"爸爸想告诉你一件事"），机械关键词匹配区分不出"这是
   叙事者描述说话动作"还是"这就是角色要说的话"，命中后按 Step 6 的
   自查清单人工判断，不要看到 warning 就直接删字/改写；
7. 大场景原文里直引号 `"` 数量为奇数——说明基于全局奇偶配对的引号
   提取可能已经错位（同一左右字符的直引号没法像 `“”「」『』` 那样
   按左右区分，一旦原文出现单个不成对的 `"` 就会导致其后所有引号
   片段归属错位），提示人工复核该大场景的对话边界是否被切错。

退出码非 0（即 errors 非空）时不允许把状态标记为 `planned`，也不允许
交付给下游；errors 为空、只有 warnings 时允许通过，但 warnings 不能
直接无视——按 Step 7 的处理方式过一遍。

**为什么把关键词检查从 error 降级成了 warning**：早期版本把命中关键词
直接当 error 拦截，实测会把正常台词误判掉——比如"我可以关掉监控30秒。
足够你做一件事：告诉你的女儿，你是个什么样的人"这种台词本身就包含
"告诉"二字，是角色要说的完整内容，硬拦下来只会逼着 Agent 删字或改写
台词，反而违反"对话严禁改写"的更高优先级规则。语义判断这件事本质上
需要理解上下文，不是字符串匹配能可靠完成的，所以改成非阻断提示，交
还给 Agent 判断。

**向用户展示**：本大场景切出的小场景数量、每个小场景的旁白+对话摘要、
新回补的角色/地点（如果有）、校验通过结果。

## 已知限制

- 对话真实性校验是子串匹配+引号片段匹配，无法识别"原文对话被拆成两半
  分别摘录"是否保持了原文语义完整，仍需自查拆分是否合理；
- 动作/神态提示词列表既是有限枚举（覆盖不到的动作描写用词仍可能漏检），
  也会对合法台词产生误报（词本身就是台词内容而非动作描写），所以只
  作为 warning 提示而非 error，最终判断依赖 Agent 对上下文的理解，
  脚本本身做不到可靠判定；
- 直引号 `"` 的引号片段提取依赖全文奇偶配对，原文里出现任何一个不成
  对的直引号（引用书名/术语等）都会导致后续片段错位，脚本只能靠"总数
  是否为奇数"这个弱信号提示风险，无法定位到具体是哪个引号导致的错位，
  也无法覆盖"错位后总数恰好还是偶数"的情况；
- `speaker` 判断依赖 Agent 对上下文的理解，脚本只能校验"speaker 在不
  在 uses_characters 里"，无法校验"这句话是不是真的这个角色说的"。

## 下一步

单个大场景处理完（`status: planned`）后，回到 `macro_scenes.yaml` 检查
是否还有其他 `pending` 的大场景，有则继续处理下一个；也可以对已
`planned` 的大场景直接推进阶段4（不需要等全部大场景都规划完）。
