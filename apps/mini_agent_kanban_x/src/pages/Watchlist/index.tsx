import { Button, Card, Collapse, Empty, List, Space, Statistic, Table, Tag, Typography, message } from "antd";
import { useWatchlist } from "../../hooks/useWatchlist";

const { Title, Text } = Typography;

/** epoch 秒 → 本地时间文本；服务端已给 created_at_text 时不会走到这里，仅作旧后端兜底。 */
function fmtReportTs(ts?: number): string {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

export default function Watchlist() {
  const { watchlist, reportTiers, pendingReports, dispatchLog, ackReport } = useWatchlist();

  const watchlistColumns = [
    { title: "ID", dataIndex: "id" },
    { title: "名称", dataIndex: "name" },
    {
      title: "启用",
      dataIndex: "enabled",
      render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag>已暂停</Tag>),
    },
  ];

  const tierColumns = [
    { title: "Tier", dataIndex: "name" },
    { title: "Job ID", dataIndex: "job_id" },
    {
      title: "Job 启用",
      dataIndex: "job_enabled",
      render: (v: boolean | null) => (v === null ? "—" : v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
    },
    { title: "下次触发", dataIndex: "next_run_str", render: (v: string | null) => v || "—" },
    { title: "空转计数", dataIndex: "idle_streak" },
  ];

  return (
    <div>
      <Title level={4}>🔔 关注与通知</Title>

      <Space size="large" style={{ marginBottom: 16 }} wrap>
        <Statistic title="关注对象" value={watchlist.data?.items?.length || 0} />
        <Statistic title="分级汇报 Tier" value={reportTiers.data?.tiers?.length || 0} />
        <Statistic title="待处理汇报" value={pendingReports.data?.total || 0} />
      </Space>

      <Card title="关注对象列表（watchlist.yaml）" style={{ marginBottom: 16 }}>
        <Table
          rowKey={(r: any) => r.id || r.name}
          size="small"
          loading={watchlist.isLoading}
          dataSource={watchlist.data?.items || []}
          columns={watchlistColumns as any}
          pagination={false}
          locale={{ emptyText: <Empty description="暂未配置关注对象" /> }}
        />
      </Card>

      <Card title="分级汇报规则（report_tiers.yaml）" style={{ marginBottom: 16 }}>
        <Table
          rowKey={(r: any) => r.id || r.name}
          size="small"
          loading={reportTiers.isLoading}
          dataSource={reportTiers.data?.tiers || []}
          columns={tierColumns as any}
          pagination={false}
          locale={{ emptyText: <Empty description="暂未配置分级汇报" /> }}
        />
      </Card>

      <Card title="待处理汇报" style={{ marginBottom: 16 }}>
        <List
          loading={pendingReports.isLoading}
          dataSource={pendingReports.data?.reports || []}
          locale={{ emptyText: "暂无待处理汇报" }}
          renderItem={(r) => (
            <List.Item
              actions={[
                <Button
                  key="ack"
                  size="small"
                  loading={ackReport.isPending}
                  onClick={() =>
                    ackReport.mutate((r.report_id || r.id) as string, {
                      onSuccess: () => message.success("已标记已读"),
                      onError: (e: any) => message.error(e?.message || "操作失败"),
                    })
                  }
                >
                  标记已读
                </Button>,
              ]}
            >
              <Collapse
                style={{ width: "100%" }}
                items={[
                  {
                    key: (r.report_id || r.id) as string,
                    label: (
                      <>
                        <Text type="secondary">
                          {r.created_at_text || fmtReportTs(r.created_at) || "时间未知"}
                        </Text>{" "}
                        {r.category ? <Tag>{r.category}</Tag> : null}
                        {r.title || r.report_id || r.id}
                      </>
                    ),
                    children: (
                      <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                        {`汇报时间：${r.created_at_text || fmtReportTs(r.created_at) || "未知"}` +
                          (r.occurred_at_text && r.occurred_at_text !== r.created_at_text
                            ? `　事件发生：${r.occurred_at_text}`
                            : "") +
                          "\n\n"}
                        {typeof r.detail === "string" ? r.detail : JSON.stringify(r.detail, null, 2)}
                      </pre>
                    ),
                  },
                ]}
              />
            </List.Item>
          )}
        />
      </Card>

      <Card title="通知发送记录">
        <List
          loading={dispatchLog.isLoading}
          size="small"
          dataSource={dispatchLog.data?.entries || []}
          locale={{ emptyText: "暂无发送记录" }}
          renderItem={(e) => (
            <List.Item>
              <Text type="secondary">{e.ts}</Text> <Tag>{e.channel}</Tag> {e.title}
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}
