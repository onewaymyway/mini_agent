import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Empty, Input, List, Row, Space, Tag, Typography } from "antd";
import { SendOutlined, StopOutlined } from "@ant-design/icons";
import { useUiStore } from "../../stores/uiStore";
import { useChatStream } from "../../hooks/useChatStream";
import PermissionsPanel from "../../components/PermissionsPanel";
import EventTimeline from "../../components/EventTimeline";

const { TextArea } = Input;

export default function Chat() {
  const sessionId = useUiStore((s) => s.currentSessionId);
  const { items, isLoadingHistory, send, interrupt } = useChatStream(sessionId || undefined);
  const [draft, setDraft] = useState("");
  const listEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items.length, items[items.length - 1]?.content]);

  const onSend = () => {
    const text = draft.trim();
    if (!text || send.isPending) return;
    send.mutate(text);
    setDraft("");
  };

  return (
    <Row gutter={16}>
      <Col span={17}>
        <Card
          title={
            <Space>
              对话{sessionId ? <Tag>session: {sessionId}</Tag> : <Tag color="default">默认会话</Tag>}
            </Space>
          }
          extra={
            <Button danger icon={<StopOutlined />} onClick={() => interrupt.mutate()} loading={interrupt.isPending}>
              中断
            </Button>
          }
        >
          <PermissionsPanel />

          {send.isError && (
            <Alert type="error" showIcon style={{ marginBottom: 12 }} message={(send.error as Error).message} />
          )}

          <div style={{ height: "52vh", overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, padding: 12 }}>
            {isLoadingHistory ? (
              <Typography.Text type="secondary">加载历史记录…</Typography.Text>
            ) : items.length === 0 ? (
              <Empty description="暂无对话记录，输入消息开始吧" />
            ) : (
              <List
                dataSource={items}
                renderItem={(item, idx) => (
                  <List.Item key={idx} style={{ border: "none", padding: "8px 0", alignItems: "flex-start" }}>
                    <div style={{ width: "100%" }}>
                      <Tag color={item.role === "user" ? "blue" : "green"}>{item.role}</Tag>
                      {"pending" in item && item.pending ? <Tag color="processing">生成中…</Tag> : null}
                      <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{item.content}</div>
                    </div>
                  </List.Item>
                )}
              />
            )}
            <div ref={listEndRef} />
          </div>

          <Space.Compact style={{ width: "100%", marginTop: 12 }}>
            <TextArea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  onSend();
                }
              }}
              placeholder="输入消息，Enter 发送，Shift+Enter 换行"
              autoSize={{ minRows: 2, maxRows: 6 }}
            />
            <Button type="primary" icon={<SendOutlined />} onClick={onSend} loading={send.isPending}>
              发送
            </Button>
          </Space.Compact>
        </Card>
      </Col>
      <Col span={7}>
        <Card title="📡 事件流" size="small">
          <EventTimeline sessionId={sessionId || undefined} />
        </Card>
      </Col>
    </Row>
  );
}
