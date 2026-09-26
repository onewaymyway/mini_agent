"""core/event_bus.py 的单元测试。

对应 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`
Sprint 2-1："最小事件总线：`core/event_bus.py`：一个进程内的
`publish(event)` / `subscribe(type, handler)`"任务的验证，以及止损条件
（"订阅者异常不应传播到发布方"）。
"""

from __future__ import annotations

import pytest

from mini_agent.core import Event, EventBus, get_event_bus, reset_event_bus


@pytest.fixture(autouse=True)
def _reset_default_bus():
    """避免测试之间通过全局单例互相污染订阅关系。"""
    reset_event_bus()
    yield
    reset_event_bus()


def test_subscribe_and_publish_delivers_event_to_matching_handler():
    bus = EventBus()
    received = []
    bus.subscribe("GoalCreated", received.append)

    ev = Event(kind="GoalCreated", payload={"goal_text": "写周报"})
    bus.publish(ev)

    assert received == [ev]


def test_publish_does_not_notify_handlers_of_other_kinds():
    bus = EventBus()
    received = []
    bus.subscribe("GoalCreated", received.append)

    bus.publish(Event(kind="ActionStarted"))

    assert received == []


def test_publish_with_no_subscribers_does_not_raise():
    bus = EventBus()
    bus.publish(Event(kind="GoalCreated"))  # 不应抛异常


def test_subscriber_exception_does_not_propagate_to_publisher():
    """止损条件：Event 只应是旁路记录，订阅者出错不能影响发布方主流程。"""
    bus = EventBus()

    def _bad_handler(_event):
        raise RuntimeError("boom")

    bus.subscribe("GoalCreated", _bad_handler)
    bus.publish(Event(kind="GoalCreated"))  # 不应抛出 RuntimeError


def test_subscriber_exception_does_not_block_other_subscribers():
    bus = EventBus()
    received = []

    def _bad_handler(_event):
        raise RuntimeError("boom")

    bus.subscribe("GoalCreated", _bad_handler)
    bus.subscribe("GoalCreated", received.append)

    ev = Event(kind="GoalCreated")
    bus.publish(ev)

    assert received == [ev]


def test_unsubscribe_stops_further_delivery():
    bus = EventBus()
    received = []

    def handler(event):
        received.append(event)

    bus.subscribe("GoalCreated", handler)
    bus.unsubscribe("GoalCreated", handler)
    bus.publish(Event(kind="GoalCreated"))

    assert received == []


def test_get_event_bus_returns_process_wide_singleton():
    assert get_event_bus() is get_event_bus()


def test_reset_event_bus_clears_singleton_subscriptions():
    received = []
    get_event_bus().subscribe("GoalCreated", received.append)

    reset_event_bus()
    get_event_bus().publish(Event(kind="GoalCreated"))

    assert received == []
