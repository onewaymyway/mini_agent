import { Alert, Button, Card, Empty, Input, List, Space, Tag, Typography } from "antd";
import { useState } from "react";
import { usePendingApprovals } from "../hooks/usePermissions";

/**
 * 内联权限 / 交互审批面板，用在 Chat 页面里（对应旧看板 Topbar + Chat Tab 内的
 * "待审批权限请求" / "待回答的交互请求" 展开面板）。
 */
export default function PermissionsPanel() {
  const { permissions, interactions, respondPerm, respondIx } = usePendingApprovals();
  const [answers, setAnswers] = useState<Record<string, string>>({});

  if (!permissions.length && !interactions.length) return null;

  return (
    <Space direction="vertical" style={{ width: "100%", marginBottom: 12 }}>
      {permissions.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`有 ${permissions.length} 个待审批权限请求`}
          description={
            <List
              size="small"
              dataSource={permissions}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Button
                      key="allow"
                      size="small"
                      type="primary"
                      loading={respondPerm.isPending}
                      onClick={() => respondPerm.mutate({ reqId: item.req_id, decision: "allow" })}
                    >
                      允许一次
                    </Button>,
                    <Button
                      key="always"
                      size="small"
                      loading={respondPerm.isPending}
                      onClick={() =>
                        respondPerm.mutate({ reqId: item.req_id, decision: "allow", remember: true })
                      }
                    >
                      始终允许
                    </Button>,
                    <Button
                      key="deny"
                      size="small"
                      danger
                      loading={respondPerm.isPending}
                      onClick={() => respondPerm.mutate({ reqId: item.req_id, decision: "deny" })}
                    >
                      拒绝
                    </Button>,
                  ]}
                >
                  <Space direction="vertical" size={0}>
                    {item.tool ? <Tag>{item.tool}</Tag> : null}
                    <Typography.Text>{item.summary || item.req_id}</Typography.Text>
                  </Space>
                </List.Item>
              )}
            />
          }
        />
      )}

      {interactions.length > 0 && (
        <Card size="small" title={`💬 待回答的交互请求（${interactions.length}）`}>
          <List
            dataSource={interactions}
            renderItem={(item) => (
              <List.Item>
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Typography.Text>{item.question || item.req_id}</Typography.Text>
                  {item.options && item.options.length > 0 ? (
                    <Space wrap>
                      {item.options.map((opt) => (
                        <Button
                          key={opt}
                          size="small"
                          onClick={() => respondIx.mutate({ reqId: item.req_id, answer: opt })}
                        >
                          {opt}
                        </Button>
                      ))}
                    </Space>
                  ) : (
                    <Space.Compact style={{ width: "100%" }}>
                      <Input
                        placeholder="输入回答"
                        value={answers[item.req_id] || ""}
                        onChange={(e) => setAnswers((prev) => ({ ...prev, [item.req_id]: e.target.value }))}
                        onPressEnter={() =>
                          respondIx.mutate({ reqId: item.req_id, answer: answers[item.req_id] || "" })
                        }
                      />
                      <Button
                        type="primary"
                        loading={respondIx.isPending}
                        onClick={() =>
                          respondIx.mutate({ reqId: item.req_id, answer: answers[item.req_id] || "" })
                        }
                      >
                        提交
                      </Button>
                    </Space.Compact>
                  )}
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}
    </Space>
  );
}

export function EmptyPlaceholder({ text }: { text: string }) {
  return <Empty description={text} />;
}
