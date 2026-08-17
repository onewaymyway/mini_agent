import { useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useCronActions, useCronJobPrompt, useCronJobRunEvents, useCronJobWorkspace, useCronJobs } from "../../hooks/useCron";

const { Title, Text, Paragraph } = Typography;

export default function CronJobs() {
  const { data, isLoading } = useCronJobs();
  const actions = useCronActions();

  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [workspaceJobId, setWorkspaceJobId] = useState<string | undefined>(undefined);
  const [feedbackTarget, setFeedbackTarget] = useState<string | undefined>(undefined);
  const [feedbackText, setFeedbackText] = useState("");
  const [promptEditing, setPromptEditing] = useState(false);
  const [promptText, setPromptText] = useState("");
  const [runEventsRunId, setRunEventsRunId] = useState<string | undefined>(undefined);

  const { data: workspace } = useCronJobWorkspace(workspaceJobId);
  const { data: promptData } = useCronJobPrompt(workspaceJobId);
  const { data: runEvents } = useCronJobRunEvents(workspaceJobId, runEventsRunId);

  const jobs = data?.jobs || [];

  return (
    <Card
      title="⏰ Cron 任务"
      extra={
        <Button icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建任务
        </Button>
      }
    >
      {data?.note && <Text type="secondary">{data.note}</Text>}
      <List
        loading={isLoading}
        dataSource={jobs}
        locale={{ emptyText: <Empty description="暂无 cron job" /> }}
        renderItem={(j) => (
          <List.Item
            actions={[
              <a key="workspace" onClick={() => setWorkspaceJobId(j.id)}>
                工作区
              </a>,
              <a key="run" onClick={() => actions.runNow.mutate(j.id, { onSuccess: () => message.success("已触发") })}>
                立即运行
              </a>,
              <a
                key="toggle"
                onClick={() => actions.update.mutate({ jobId: j.id, body: { enabled: !j.enabled } })}
              >
                {j.enabled ? "禁用" : "启用"}
              </a>,
              <a key="feedback" onClick={() => { setFeedbackTarget(j.id); setFeedbackText(""); }}>
                提意见
              </a>,
              !j.is_system && (
                <a
                  key="delete"
                  onClick={() =>
                    Modal.confirm({
                      title: `删除任务「${j.name || j.id}」？`,
                      onOk: () => actions.remove.mutate(j.id, { onSuccess: () => message.success("已删除") }),
                    })
                  }
                >
                  删除
                </a>
              ),
            ].filter(Boolean)}
          >
            <List.Item.Meta
              title={
                <Space wrap>
                  {j.name || j.id}
                  <Tag color={j.enabled ? "green" : "default"}>{j.enabled ? "已启用" : "已禁用"}</Tag>
                  {j.execution_phase && <Tag color="processing">{j.execution_phase}</Tag>}
                  {j.is_system && <Tag>系统内置</Tag>}
                  <InputNumber
                    size="small"
                    min={0}
                    max={100}
                    value={j.priority}
                    style={{ width: 72 }}
                    onChange={(v) => actions.update.mutate({ jobId: j.id, body: { priority: v ?? undefined } })}
                  />
                </Space>
              }
              description={
                <>
                  <div>{j.description}</div>
                  <Text type="secondary">
                    schedule: {j.schedule} · 下次: {j.next_run_str || j.next_run_at || "-"} · 已跑 {j.run_count ?? 0} 次
                  </Text>
                </>
              }
            />
          </List.Item>
        )}
      />

      <Modal title="新建 cron job" open={createOpen} onCancel={() => setCreateOpen(false)} footer={null}>
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => {
            actions.create.mutate(values, {
              onSuccess: () => {
                message.success("已创建");
                setCreateOpen(false);
                form.resetFields();
              },
            });
          }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="schedule" label="cron 表达式" rules={[{ required: true }]}>
            <Input placeholder="例如 0 9 * * *" />
          </Form.Item>
          <Form.Item name="task_template" label="任务 Prompt" rules={[{ required: true }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="priority" label="优先级">
            <InputNumber min={0} max={100} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={actions.create.isPending}>
            创建
          </Button>
        </Form>
      </Modal>

      <Modal
        title="提意见反馈"
        open={!!feedbackTarget}
        onCancel={() => setFeedbackTarget(undefined)}
        onOk={() => {
          if (feedbackTarget) actions.feedback.mutate({ jobId: feedbackTarget, text: feedbackText });
          setFeedbackTarget(undefined);
        }}
      >
        <Input.TextArea rows={4} value={feedbackText} onChange={(e) => setFeedbackText(e.target.value)} placeholder="持久化写入 description/task_template" />
      </Modal>

      <Drawer
        title={`任务工作区：${workspaceJobId || ""}`}
        open={!!workspaceJobId}
        onClose={() => {
          setWorkspaceJobId(undefined);
          setPromptEditing(false);
          setRunEventsRunId(undefined);
        }}
        width={640}
      >
        {workspace && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="运行中">{workspace.is_running ? "是" : "否"}</Descriptions.Item>
              <Descriptions.Item label="状态摘要">{JSON.stringify(workspace.state)}</Descriptions.Item>
            </Descriptions>

            <Title level={5}>
              专属 Prompt
              {!promptEditing && (
                <Button
                  size="small"
                  style={{ marginLeft: 8 }}
                  onClick={() => {
                    setPromptEditing(true);
                    setPromptText(promptData?.prompt || "");
                  }}
                >
                  编辑
                </Button>
              )}
            </Title>
            {promptEditing ? (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Input.TextArea rows={6} value={promptText} onChange={(e) => setPromptText(e.target.value)} />
                <Space>
                  <Button
                    type="primary"
                    loading={actions.updatePrompt.isPending}
                    onClick={() =>
                      actions.updatePrompt.mutate(
                        { jobId: workspaceJobId as string, prompt: promptText },
                        { onSuccess: () => { message.success("已保存"); setPromptEditing(false); } }
                      )
                    }
                  >
                    保存
                  </Button>
                  <Button onClick={() => setPromptEditing(false)}>取消</Button>
                </Space>
              </Space>
            ) : (
              <Paragraph style={{ whiteSpace: "pre-wrap" }}>{promptData?.prompt}</Paragraph>
            )}

            <Space style={{ marginBottom: 12 }}>
              <Button
                danger
                loading={actions.reset.isPending}
                onClick={() =>
                  Modal.confirm({
                    title: "重置任务？",
                    content: "清空进度摘要，下次触发从头开始，仅用于卡死恢复。",
                    onOk: () => actions.reset.mutate(workspaceJobId as string),
                  })
                }
              >
                重置（卡死恢复）
              </Button>
            </Space>

            <Title level={5}>最近执行记录</Title>
            <List
              size="small"
              dataSource={workspace.recent_runs_summary || []}
              locale={{ emptyText: "暂无执行记录" }}
              renderItem={(r: any) => (
                <List.Item actions={[<a key="v" onClick={() => setRunEventsRunId(r.run_id)}>查看事件</a>]}>
                  <Space>
                    <Tag color={r.success ? "green" : "red"}>{r.success ? "成功" : "失败"}</Tag>
                    {r.run_id} {r.started_at}
                  </Space>
                </List.Item>
              )}
            />

            {runEventsRunId && (
              <>
                <Title level={5}>执行事件（{runEventsRunId}）</Title>
                <List
                  size="small"
                  dataSource={(runEvents?.events as any[]) || []}
                  renderItem={(e: any) => (
                    <List.Item>
                      <Text type="secondary">{e.ts}</Text> {e.summary || JSON.stringify(e)}
                    </List.Item>
                  )}
                />
              </>
            )}
          </>
        )}
      </Drawer>
    </Card>
  );
}
