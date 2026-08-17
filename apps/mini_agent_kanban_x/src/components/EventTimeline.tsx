import { Empty, Tag, Timeline, Typography } from "antd";
import { useEventsPanel } from "../hooks/useEventsPanel";

/** 事件流面板：展示原始 Agent 事件时间线（对应旧看板 Chat Tab 的"📡 事件流"）。 */
export default function EventTimeline({ sessionId }: { sessionId?: string }) {
  const { events } = useEventsPanel(sessionId);

  if (events.length === 0) return <Empty description="暂无事件" />;

  return (
    <div style={{ maxHeight: 360, overflow: "auto" }}>
      <Timeline
        items={events
          .slice()
          .reverse()
          .map((e, idx) => ({
            key: idx,
            children: (
              <div>
                <Tag>{e.event || "event"}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {e.turn_id ? `turn: ${e.turn_id}` : ""}
                </Typography.Text>
                <pre style={{ margin: "4px 0 0", fontSize: 12, whiteSpace: "pre-wrap" }}>
                  {typeof e.data === "string" ? e.data : JSON.stringify(e.data)}
                </pre>
              </div>
            ),
          }))}
      />
    </div>
  );
}
