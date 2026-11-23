import pytest

from article_garden import domains, paths, settings, store


def test_conservative_defaults_for_new_domain():
    ctx = domains.create_domain("ai", "AI", "ai")
    eff, warns = settings.effective_settings(ctx)
    assert warns == []
    a = eff["automation"]
    assert a["gear"] == "G0" and not any(v for k, v in a.items() if k != "gear")
    assert eff["budget"]["max_repair_rounds"] == 6 and eff["budget"]["step_timeout_minutes"] == 40
    assert eff["llm"]["use_system_proxy"] is False


def test_two_layer_precedence_and_clear():
    ctx = domains.create_domain("ai", "AI")
    settings.save_override("global", "budget", "max_topics_per_cycle", 9)
    assert settings.effective_settings(ctx)[0]["budget"]["max_topics_per_cycle"] == 9
    settings.save_override("domain", "budget", "max_topics_per_cycle", 2, ctx)
    assert settings.effective_settings(ctx)[0]["budget"]["max_topics_per_cycle"] == 2
    assert ("budget", "max_topics_per_cycle") in settings.override_keys(ctx)
    settings.clear_override("domain", "budget", "max_topics_per_cycle", ctx)
    assert settings.effective_settings(ctx)[0]["budget"]["max_topics_per_cycle"] == 9  # 恢复继承
    assert not ctx.settings_path.read_text(encoding="utf-8").strip() or "budget" not in ctx.settings_path.read_text(encoding="utf-8")


def test_automation_is_domain_only_and_not_inherited():
    a = domains.create_domain("a1", "A")
    b = domains.create_domain("b1", "B")
    settings.save_override("domain", "automation", "gear", "G2", a)
    assert settings.effective_settings(a)[0]["automation"]["gear"] == "G2"
    assert settings.effective_settings(b)[0]["automation"]["gear"] == "G0"  # 不继承其它库的放权
    with pytest.raises(ValueError):
        settings.save_override("global", "automation", "gear", "G3")
    # 手写进全局文件的 automation 项被忽略并告警
    store.write_yaml_atomic(paths.global_settings_path(), {"automation": {"gear": "G3"}})
    eff, warns = settings.effective_settings(b)
    assert eff["automation"]["gear"] == "G0" and any("只能在文章库里设置" in w for w in warns)


def test_global_only_item_cannot_be_set_in_domain():
    ctx = domains.create_domain("ai", "AI")
    with pytest.raises(ValueError):
        settings.save_override("domain", "budget", "global_max_llm_calls_per_day", 1, ctx)
    settings.save_override("global", "budget", "global_max_llm_calls_per_day", 100)
    assert settings.effective_settings(ctx)[0]["budget"]["global_max_llm_calls_per_day"] == 100


@pytest.mark.parametrize("group,key,value", [
    ("automation", "gear", "G9"), ("automation", "open_topics", "yes"), ("budget", "max_repair_rounds", 0),
    ("budget", "max_topics_per_cycle", 1.5), ("budget", "max_topics_per_cycle", True),
    ("thresholds", "overlap_max", 1.5), ("search", "provider", "bing"), ("nope", "x", 1),
])
def test_invalid_values_rejected_on_save(group, key, value):
    ctx = domains.create_domain("ai", "AI")
    with pytest.raises(ValueError):
        settings.save_override("domain", group, key, value, ctx)


def test_invalid_file_values_fall_back_with_warnings():
    ctx = domains.create_domain("ai", "AI")
    store.write_yaml_atomic(ctx.settings_path, {"budget": {"max_repair_rounds": -3, "bogus": 1}, "weird": {"a": 1},
                                                "thresholds": "nope"})
    eff, warns = settings.effective_settings(ctx)
    assert eff["budget"]["max_repair_rounds"] == 6 and len(warns) == 4


def test_unparsable_settings_file_ignored_with_warning():
    ctx = domains.create_domain("ai", "AI")
    ctx.settings_path.write_text("a: [x", encoding="utf-8")
    eff, warns = settings.effective_settings(ctx)
    assert eff == settings.defaults() and warns
