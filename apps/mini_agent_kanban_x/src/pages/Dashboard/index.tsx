import { Alert, Card, Col, Descriptions, Row, Skeleton, Statistic, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useStatus, useWhoami } from "../../hooks/useStatus";
import { getDiagnostics } from "../../api/endpoints";
import { useSessions } from "../../hooks/useSessions";

export default function Dashboard() {
  const { data: status, isError: statusError, error } = useStatus();
  const { data: whoami } = useWhoami();
  const { data: sessions } = useSessions();
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: getDiagnostics,
    refetchInterval: 10000,
  });

  return (
    <div>
      {statusError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="无法连接到 mini-agent daemon"
          description={(error as Error)?.message}
        />
      )}

      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="Agent 状态" value={status?.state ?? "-"} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="当前模型" value={String(status?.model ?? "-")} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="会话数" value={sessions?.sessions?.length ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="SSE 订阅数" value={Number(status?.sse_subscribers ?? 0)} />
          </Card>
        </Col>
      </Row>

      <Card title="身份 / 权限" style={{ marginTop: 16 }}>
        {whoami ? (
          <Descriptions column={3} size="small">
            <Descriptions.Item label="用户 ID">{String(whoami.user_id ?? "-")}</Descriptions.Item>
            <Descriptions.Item label="角色">{String(whoami.role ?? "-")}</Descriptions.Item>
            <Descriptions.Item label="Owner">
              {whoami.is_owner ? <Tag color="gold">是</Tag> : <Tag>否</Tag>}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Skeleton active paragraph={{ rows: 1 }} />
        )}
      </Card>

      <Card title="诊断信息（/v1/diagnostics）" style={{ marginTop: 16 }}>
        {diagnostics.isLoading ? (
          <Skeleton active />
        ) : diagnostics.isError ? (
          <Alert type="warning" message="诊断接口暂不可用" showIcon />
        ) : (
          <pre style={{ maxHeight: 360, overflow: "auto", background: "#fafafa", padding: 12 }}>
            {JSON.stringify(diagnostics.data, null, 2)}
          </pre>
        )}
      </Card>
    </div>
  );
}
