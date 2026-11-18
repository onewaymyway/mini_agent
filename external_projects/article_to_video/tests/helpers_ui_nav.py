"""R7 界面导航辅助：大纲、口播稿、配音、分镜、合成导出现在位于驾驶舱的步骤区内。"""

COCKPIT = "🧭 驾驶舱"
STEP_LABELS = {"🗺️ 大纲", "📝 口播稿", "🔊 配音", "🎞️ 分镜", "🎬 合成导出", "🧭 总控"}


def go_page(at, label: str):
    """切换侧边栏页面（驾驶舱、新建项目、项目、设置）。"""
    return at.sidebar.radio[0].set_value(label).run()


def go_step(at, label: str):
    """进入驾驶舱并选择某个步骤（大纲等）。"""
    go_page(at, COCKPIT)
    radio = next(r for r in at.radio if r.key == "cockpit_step")
    return radio.set_value(label).run()
