import { useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useGoalActions, useGoals } from "../../hooks/useGoals";
import type { GoalNode, ObjectiveNode } from "../../api/types";
import GoalDetailDrawer from "./GoalDetailDrawer";

const COLUMNS: { key: string; title: string; color: string }[] = [
  { key: "active", title: "进行中", color: "blue" },
  { key: "paused", title: "已暂停", color: "orange" },
  { key: "completed", title: "已完成", color: "green" },
  { key: "abandoned", title: "已放弃", color: "default" },
];

function GoalCard({ goal, objectives, onOpen }: { goal: GoalNode; objectives: ObjectiveNode[]; onOpen: () => void }) {
  const related = objectives.filter((o) => o.goal_id === goal.id);
  return (
    <Card size="small" hoverable onClick={onOpen} style={{ marginBottom: 8 }}>
      <Typography.Text strong>{goal.title}</Typography.Text>
      <div style={{ marginTop: 4 }}>
        <Tag color="purple">优先级 {goal.priority ?? "-"}</Tag>
        {goal.is_recurring ? <Tag color="cyan">周期性</Tag> : null}
        {goal.execution_spec_confirmed ? <Tag color="green">规范已确认</Tag> : <Tag>规范未确认</Tag>}
      </div>
      {goal.work_thread_progress && (
        <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ marginTop: 6, marginBottom: 0 }}>
          {goal.work_thread_progress}
        </Typography.Paragraph>
      )}
      {related.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <Badge count={related.length} size="small" /> <Typography.Text type="secondary">关联执行</Typography.Text>
        </div>
      )}
    </Card>
  );
}

/** 目标看板：旧看板规模最大的 Tab（方案文档 P5a/P5b），拆成看板列表 + 详情抽屉两层。 */
export default function Goals() {
  const { data, isLoading, isError, error, refetch } = useGoals();
  const { create } = useGoalActions();
  const [createOpen, setCreateOpen] = useState(false);
  const [activeGoalId, setActiveGoalId] = useState<string | undefined>(undefined);
  const [form] = Form.useForm();

  const goals = data?.goals || [];
  const objectives = data?.objectives || [];

  const grouped = useMemo(() => {
    const map: Record<string, GoalNode[]> = {};
    for (const col of COLUMNS) map[col.key] = [];
    for (const g of goals) {
      const key = COLUMNS.some((c) => c.key === g.status) ? (g.status as string) : "active";
      (map[key] ||= []).push(g);
    }
    return map;
  }, [goals]);

  return (
    <div>
      {isError && <Alert type="error" showIcon message={(error as Error).message} style={{ marginBottom: 12 }} />}

      <Space style={{ marginBottom: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          刷新
        </Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建目标
        </Button>
      </Space>

      <Row gutter={12}>
        {COLUMNS.map((col) => (
          <Col span={6} key={col.key}>
            <Card
              size="small"
              title={
                <Space>
                  <Tag color={col.color}>{col.title}</Tag>
                  <Typography.Text type="secondary">{grouped[col.key]?.length ?? 0}</Typography.Text>
                </Space>
              }
              loading={isLoading}
              styles={{ body: { minHeight: 300, maxHeight: "68vh", overflow: "auto" } }}
            >
              {(grouped[col.key] || []).length === 0 ? (
                <Empty description="暂无" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                grouped[col.key].map((g) => (
                  <GoalCard key={g.id} goal={g} objectives={objectives} onOpen={() => setActiveGoalId(g.id)} />
                ))
              )}
            </Card>
          </Col>
        ))}
      </Row>

      <GoalDetailDrawer
        goalId={activeGoalId}
        goal={goals.find((g) => g.id === activeGoalId)}
        objectives={objectives.filter((o) => o.goal_id === activeGoalId)}
        onClose={() => setActiveGoalId(undefined)}
      />

      <Modal
        title="新建目标"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ priority: 50 }}
          onFinish={(values) => {
            create.mutate(values, {
              onSuccess: () => {
                message.success("已创建");
                setCreateOpen(false);
                form.resetFields();
              },
            });
          }}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="priority" label="优先级">
            <InputNumber min={0} max={100} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
