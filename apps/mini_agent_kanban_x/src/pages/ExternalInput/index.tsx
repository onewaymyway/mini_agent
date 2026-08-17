import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  List,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useExternalInput } from "../../hooks/useExternalInput";
import type { ExternalInputSource } from "../../api/types";

const { Title, Text, Paragraph } = Typography;

export default function ExternalInput() {
  const { sources, policies, alerts, events, candidates, feedbackLoop, reload, confirmCandidate, dismissCandidate } =
    useExternalInput();

  const sourceColumns = [
    { title: "ID", dataIndex: "id" },
    { title: "类型", dataIndex: "type" },
    {
      title: "启用",
      dataIndex: "enabled",
      render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
    },
    { title: "轮询间隔(s)", dataIndex: "interval_seconds" },
    {
      title: "运行中",
      dataIndex: "is_running",
      render: (v: boolean | null) => (v === null ? "—" : v ? <Tag color="blue">运行中</Tag> : <Tag>未运行</Tag>),
    },
    { title: "上次轮询", dataIndex: "last_poll_ts", render: (v: string | null) => v || "—" },
    {
      title: "连续失败",
      dataIndex: "consecutive_failures",
      render: (v: number, r: ExternalInputSource) =>
        r.circuit_open ? <Tag color="red">熔断（{v}）</Tag> : v,
    },
    { title: "最近错误", dataIndex: "last_error", render: (v: string | null) => v || "—" },
  ];

  return (
    <div>
      <Title level={4}>🔌 外部输入网关</Title>

      <Card
        title="已注册来源"
        style={{ marginBottom: 16 }}
        extra={
          <Button
            icon={<ReloadOutlined />}
            loading={reload.isPending}
            onClick={() =>
              reload.mutate(undefined, {
                onSuccess: () => message.success("已重新加载 sources.yaml"),
                onError: (e: any) => message.error(e?.message || "重载失败"),
              })
            }
          >
            重载 sources.yaml
          </Button>
        }
      >
        {sources.data && !sources.data.poller_available && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="GatewayPoller 当前不可用（非 daemon 模式，或启动时构造失败），健康字段为空"
          />
        )}
        <Table
          rowKey="id"
          size="small"
          loading={sources.isLoading}
          dataSource={sources.data?.sources || []}
          columns={sourceColumns as any}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无已配置的来源" /> }}
        />
      </Card>

      <Card title="路由规则（policies.yaml，只读）" style={{ marginBottom: 16 }}>
        {(policies.data as any)?._error && <Alert type="error" message={(policies.data as any)._error} style={{ marginBottom: 12 }} />}
        <List
          loading={policies.isLoading}
          dataSource={policies.data?.rules || []}
          locale={{ emptyText: "暂无路由规则" }}
          renderItem={(r, idx) => (
            <List.Item>
              <Text type="secondary">#{idx + 1}</Text>&nbsp;
              <Tag>{r.action}</Tag>
              <Text code>{JSON.stringify(r.match)}</Text>
              {r.enqueue ? <Tag color="blue">enqueue</Tag> : null}
            </List.Item>
          )}
        />
      </Card>

      <Card title="待处理告警" style={{ marginBottom: 16 }}>
        <Statistic title="未处理告警数" value={alerts.data?.total || 0} style={{ marginBottom: 12 }} />
        <List
          loading={alerts.isLoading}
          dataSource={alerts.data?.alerts || []}
          locale={{ emptyText: "暂无待处理告警" }}
          renderItem={(a) => (
            <List.Item>
              <List.Item.Meta
                title={a.title || a.id}
                description={
                  <>
                    <Text type="secondary">{a.created_at}</Text> {a.source_id ? <Tag>{a.source_id}</Tag> : null}
                    <div>{a.detail}</div>
                  </>
                }
              />
            </List.Item>
          )}
        />
      </Card>

      <Card title="新颖信号候选" style={{ marginBottom: 16 }}>
        <List
          loading={candidates.isLoading}
          dataSource={candidates.data?.candidates || []}
          locale={{ emptyText: "暂无待确认候选" }}
          renderItem={(c) => (
            <List.Item
              actions={[
                <Button
                  key="confirm"
                  size="small"
                  type="primary"
                  loading={confirmCandidate.isPending}
                  onClick={() =>
                    confirmCandidate.mutate(c.id, {
                      onSuccess: (r) => message.success(`已创建目标：${r.goal_title || r.goal_id}`),
                      onError: (e: any) => message.error(e?.message || "确认失败"),
                    })
                  }
                >
                  确认为目标
                </Button>,
                <Button
                  key="dismiss"
                  size="small"
                  loading={dismissCandidate.isPending}
                  onClick={() => dismissCandidate.mutate(c.id, { onError: (e: any) => message.error(e?.message || "忽略失败") })}
                >
                  忽略
                </Button>,
              ]}
            >
              <List.Item.Meta title={c.title || c.id} description={c.summary} />
            </List.Item>
          )}
        />
      </Card>

      <Card title="最近事件流水" style={{ marginBottom: 16 }}>
        <List
          loading={events.isLoading}
          size="small"
          dataSource={events.data?.events || []}
          locale={{ emptyText: "暂无 external.* 事件" }}
          renderItem={(e) => (
            <List.Item>
              <Text type="secondary">{e.ts}</Text> <Tag>{e.event_type}</Tag>
            </List.Item>
          )}
        />
      </Card>

      <Card title="外部知识反馈闭环（P1~P5）">
        <Tabs
          items={[
            {
              key: "p1",
              label: "候选队列过期巡检",
              children: (
                <Descriptions size="small" column={2} bordered>
                  {Object.entries((feedbackLoop.data as any)?.candidate_queue_triage || {}).map(([k, v]) => (
                    <Descriptions.Item key={k} label={k}>
                      {String(v)}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              ),
            },
            {
              key: "p2",
              label: "wiki 利用率",
              children: (
                <List
                  size="small"
                  dataSource={((feedbackLoop.data as any)?.wiki_utility_audit?.top_used || []) as any[]}
                  locale={{ emptyText: "暂无数据" }}
                  renderItem={(it: any) => (
                    <List.Item>
                      {it.page_id} — hit_count: {it.hit_count}
                    </List.Item>
                  )}
                />
              ),
            },
            {
              key: "p3",
              label: "阈值自校准",
              children: (
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="当前阈值">
                    {(feedbackLoop.data as any)?.relevance_threshold_calibration?.current_threshold}
                  </Descriptions.Item>
                  <Descriptions.Item label="上次校准时间">
                    {(feedbackLoop.data as any)?.relevance_threshold_calibration?.last_calibrated_at}
                  </Descriptions.Item>
                </Descriptions>
              ),
            },
            {
              key: "p4a",
              label: "外部趋势×能力薄弱点",
              children: (
                <Statistic
                  title="候选数"
                  value={(feedbackLoop.data as any)?.external_trend_capability_link?.candidate_count || 0}
                />
              ),
            },
            {
              key: "p4b",
              label: "生态定位扫描",
              children: (
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="轮转游标">
                    {(feedbackLoop.data as any)?.ecosystem_positioning_scan?.rotation_offset}
                  </Descriptions.Item>
                  <Descriptions.Item label="生态页面数">
                    {(feedbackLoop.data as any)?.ecosystem_positioning_scan?.ecosystem_pages_count}
                  </Descriptions.Item>
                </Descriptions>
              ),
            },
            {
              key: "p5",
              label: "月度战略回顾",
              children: (
                <Collapse
                  items={[
                    {
                      key: "latest",
                      label: `最新月份：${(feedbackLoop.data as any)?.monthly_trend_retrospective?.latest_month || "无"}`,
                      children: (
                        <Paragraph style={{ whiteSpace: "pre-wrap" }}>
                          {(feedbackLoop.data as any)?.monthly_trend_retrospective?.latest_content || "暂无内容"}
                        </Paragraph>
                      ),
                    },
                  ]}
                />
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
