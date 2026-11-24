from world_forge import repo, store


def test_read_jsonl_skips_corrupt_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n not-json \n{"a": 2}\n', encoding="utf-8")
    assert [r["a"] for r in store.read_jsonl(p)] == [1, 2]


def test_locked_is_reentrant_in_same_thread(tmp_path):
    lock = tmp_path / "locks" / "w.lock"
    with store.locked(lock):
        with store.locked(lock):  # 不应死锁
            pass


def test_append_and_write_events_roundtrip(world):
    assert repo.load_events(world, "main") == []
    repo.append_event(world, "main", {"id": "evt_1", "title": "a"})
    repo.append_event(world, "main", {"id": "evt_2", "title": "b"})
    assert [e["id"] for e in repo.load_events(world, "main")] == ["evt_1", "evt_2"]
    repo.write_events(world, "main", [{"id": "evt_9", "title": "c"}])
    assert [e["id"] for e in repo.load_events(world, "main")] == ["evt_9"]


def test_list_world_ids_and_missing_world(world):
    assert world in repo.list_world_ids()
    import pytest

    with pytest.raises(repo.WorldNotFound):
        repo.load_meta("wld_missing")
