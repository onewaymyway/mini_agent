import { Card, Collapse, Descriptions, Empty, List, Space, Statistic, Tag, Typography } from "antd";
import { useGlobalSchedule } from "../../hooks/useGlobalSchedule";

const { Title, Text } = Typography;

export default function GlobalSchedule() {
  const { cronJobs, goals, gating, fairness } = useGlobalSchedule();

  const upcomingCron = (cronJobs.data?.jobs || [])
    .filter((j) => j.enabled && j.next_run_at)
    .sort((a, b) => String(a.next_run_at).localeCompare(String(b.next_run_at)))
    .slice(0, 30);

  const recurringGoals = (goals.data?.goals || []).filter((g) => g.is_recurring);

  const objectives = (fairness.data as any)?.objectives || (fairness.data as any)?.active_objectives || [];

  return (
    <div>
      <Title level={4}>🗓️ 全局日程</Title>
      <Space size="large" style={{ marginBottom: 16 }} wrap>
        <Statistic title="24 小时内待触发 cron" value={upcomingCron.length} />
        <Statistic title="周期性 Goal" value={recurringGoals.length} />
        <Statistic title="仲裁状态变化记录" value={gating.data?.history?.length || 0} />
      </Space>

      <Card title="未来到期的 Cron 任务" style={{ marginBottom: 16 }}>
        <List
          loading={cronJobs.isLoading}
          dataSource={upcomingCron}
          locale={{ emptyText: <Empty description="暂无即将触发的任务" /> }}
          renderItem={(j) => (
            <List.Item>
              <List.Item.Meta
                title={j.name || j.id}
                description={`下次触发: ${j.next_run_str || j.next_run_at} · schedule: ${j.schedule}`}
              />
            </List.Item>
          )}
        />
      </Card>

      <Card title="周期性 Goal 下次触发" style={{ marginBottom: 16 }}>
        <List
          loading={goals.isLoading}
          dataSource={recurringGoals}
          locale={{ emptyText: <Empty description="暂无周期性 Goal" /> }}
          renderItem={(g) => (
            <List.Item>
              <List.Item.Meta title={g.title} description={g.progress_notes || g.description} />
            </List.Item>
          )}
        />
      </Card>

      <Card title="调度仲裁状态变化时间线" style={{ marginBottom: 16 }}>
        {gating.data?.ratio_summary ? (
          <Descriptions size="small" column={3} bordered style={{ marginBottom: 12 }}>
            {Object.entries(gating.data.ratio_summary as Record<string, unknown>).map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>
                {typeof v === "object" ? JSON.stringify(v) : String(v)}
              </Descriptions.Item>
            ))}
          </Descriptions>
        ) : null}
        <List
          loading={gating.isLoading}
          size="small"
          dataSource={gating.data?.history || []}
          locale={{ emptyText: "暂无仲裁状态变化记录" }}
          renderItem={(h: any) => (
            <List.Item>
              <Text type="secondary">{h.at}</Text> <Tag>{h.state}</Tag> {h.reason}
            </List.Item>
          )}
        />
      </Card>

      <Card title="调度公平性诊断（只读）">
        <Collapse
          items={[
            {
              key: "raw",
              label: "逐 objective 明细展开",
              children: (
                <List
                  loading={fairness.isLoading}
                  dataSource={objectives}
                  locale={{ emptyText: "暂无活跃 objective" }}
                  renderItem={(o: any) => (
                    <List.Item>
                      <Descriptions size="small" column={1}>
                        <Descriptions.Item label="ID">{o.id || o.objective_id}</Descriptions.Item>
                        <Descriptions.Item label="priority">{o.priority}</Descriptions.Item>
                        <Descriptions.Item label="aging_boost">{o.aging_boost}</Descriptions.Item>
                        <Descriptions.Item label="effective_priority">{o.effective_priority}</Descriptions.Item>
                      </Descriptions>
                    </List.Item>
                  )}
                />
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
