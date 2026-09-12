"""voice_mapping.py — 把角色的 voice_profile 文字描述映射到具体 TTS 音色。

方案文档：next_doc/novel_video_generator_plan_v2.md 第 4 点（角色配音）。

设计原则：
  - CosyVoice 支持零样本音色克隆，理想情况下应该用参考音频而不是这里的
    关键词映射；但本版暂未实现参考音频采集入口（同 v1 的 face_reference_id
    未实现现状一致），所以 CosyVoice 路径目前退化为"固定几个内置说话人
    按性别粗分"，等后续版本补上参考音频机制后再精细化；
  - edge-tts 提供了一批不同性别/风格的中文内置音色，本模块按
    voice_profile 里的关键词（性别/年龄段）做启发式匹配，匹配不到时
    退回默认音色（旁白默认音色，同 v1 DEFAULT_EDGE_TTS_VOICE）；
  - 这是纯规则式的关键词匹配，不追求精确，能把"男声/女声/老年/青年/
    童声"这几个最基本的维度分开即可，更细的风格差异交给 CosyVoice
    参考音频机制解决（留作后续版本）。
"""

from __future__ import annotations

# edge-tts 中文内置音色（zh-CN），按性别/年龄段粗分。
# 完整列表可用 `edge-tts --list-voices` 查询，这里只收录常用的几个。
EDGE_TTS_VOICE_TABLE = {
    "narrator_default": "zh-CN-YunxiNeural",       # 旁白默认：青年男声，沉稳
    "male_young": "zh-CN-YunxiNeural",
    "male_middle_aged": "zh-CN-YunjianNeural",
    "male_old": "zh-CN-YunyangNeural",
    "female_young": "zh-CN-XiaoxiaoNeural",
    "female_middle_aged": "zh-CN-XiaoxiaoNeural",
    "female_old": "zh-CN-XiaoqiuNeural",
    "child": "zh-CN-XiaoyiNeural",
}

# 关键词 → 分类，按顺序匹配，匹配到第一个命中的类别就返回。
_KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("child", ("童声", "孩子", "小孩", "儿童")),
    ("male_old", ("老者", "老年男", "苍老男", "老翁", "老丈")),
    ("female_old", ("老年女", "苍老女", "老妇", "老太")),
    ("male_middle_aged", ("中年男", "壮年男")),
    ("female_middle_aged", ("中年女", "壮年女")),
    ("male_young", ("男声", "男子", "少年", "公子", "郎君")),
    ("female_young", ("女声", "女子", "少女", "姑娘")),
]


def resolve_edge_tts_voice(voice_profile: str | None) -> str:
    """按 voice_profile 描述关键词匹配一个 edge-tts 内置音色名。

    匹配不到任何关键词时返回旁白默认音色（narrator_default）。
    """
    text = voice_profile or ""
    for category, keywords in _KEYWORD_RULES:
        if any(kw in text for kw in keywords):
            return EDGE_TTS_VOICE_TABLE[category]
    return EDGE_TTS_VOICE_TABLE["narrator_default"]


def resolve_cosyvoice_speaker(voice_profile: str | None) -> str | None:
    """CosyVoice 内置说话人粗分（男/女），返回 None 时由 tts_engine 用其
    自己的默认说话人（list_avaliable_spks 的第一个）。

    等后续版本接入参考音频克隆机制后，这个函数会被参考音频路径取代，
    目前只是一个占位的粗粒度实现。
    """
    text = voice_profile or ""
    female_kw = ("女声", "女子", "少女", "姑娘", "老妇", "老太", "中年女")
    if any(kw in text for kw in female_kw):
        return "中文女"
    male_kw = ("男声", "男子", "少年", "公子", "郎君", "老者", "老翁", "中年男")
    if any(kw in text for kw in male_kw):
        return "中文男"
    return None
