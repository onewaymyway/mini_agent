import { useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Popconfirm,
  Skeleton,
  Space,
  Steps,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import type { GoalNode, ObjectiveNode } from "../../api/types";
import { useCycleDiagnostics, useExecutionSpec, useGoalActions, useObjectiveActions, useTuningProposals } from "../../hooks/useGoals";
import DiffView from "../../components/DiffView";

function JsonBlock({ data }: { data: unknown }) {
  if (data === undefined || data === null) return <Empty description="无数据" />;
  return (
    <pre style={{ maxHeight: 320, overflow: "auto", background: "#fafafa", padding: 8 }}>
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function OverviewTab({ goal }: { goal: GoalNode }) {
  const { update, recur, unrecur, skipNext, lightweightNext, migrateLegacy, feedback } = useGoalActions();
  const [fb, setFb] = useState("");

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Descriptions column={2} size="small" bordered>
        <Descriptions.Item label="状态">{goal.status}</Descriptions.Item>
        <Descriptions.Item label="优先级">{goal.priority}</Descriptions.Item>
        <Descriptions.Item label="周期性">{goal.is_recurring ? "是" : "否"}</Descriptions.Item>
        <Descriptions.Item label="规范确认">{goal.execution_spec_confirmed ? "已确认" : "未确认"}</Descriptions.Item>
        <Descriptions.Item label="描述" span={2}>
          {goal.description || "-"}
        </Descriptions.Item>
        <Descriptions.Item label="进展" span={2}>
          {goal.work_thread_progress || goal.progress_notes || "-"}
        </Descriptions.Item>
      </Descriptions>

      <Space wrap>
        {goal.is_recurring ? (
          <Button onClick={() => unrecur.mutate(goal.id)} loading={unrecur.isPending}>
            取消周期性
          </Button>
        ) : (
          <Button onClick={() => recur.mutate(goal.id)} loading={recur.isPending}>
            设为周期性
          </Button>
        )}
        <Button onClick={() => skipNext.mutate(goal.id)} loading={skipNext.isPending}>
          跳过下一周期
        </Button>
        <Button onClick={() => lightweightNext.mutate(goal.id)} loading={lightweightNext.isPending}>
          轻量下一周期
        </Button>
        {goal.is_recurring ? (
          <Popconfirm
            title="迁移历史数据"
            description="下一次触发时会附加一次搬迁任务，把旧的 cycle_NNNN/ 目录内容搬进新的 output/ 结构。确认？"
            onConfirm={() =>
              migrateLegacy.mutate(goal.id, {
                onSuccess: () => message.success("已标记，下次触发这个 Goal 时会附加一次历史数据迁移任务"),
                onError: (err: unknown) =>
                  message.error(err instanceof Error ? err.message : "未检测到需要迁移的历史数据"),
              })
            }
          >
            <Button loading={migrateLegacy.isPending}>迁移历史数据</Button>
          </Popconfirm>
        ) : null}
        <Popconfirm title="确认放弃该目标？" onConfirm={() => update.mutate({ id: goal.id, body: { status: "abandoned" } })}>
          <Button danger>放弃目标</Button>
        </Popconfirm>
      </Space>

      <Input.TextArea placeholder="对该目标提意见反馈…" value={fb} onChange={(e) => setFb(e.target.value)} rows={2} />
      <Button
        type="primary"
        loading={feedback.isPending}
        disabled={!fb.trim()}
        onClick={() =>
          feedback.mutate(
            { id: goal.id, text: fb },
            {
              onSuccess: () => {
                message.success("已提交反馈");
                setFb("");
              },
            }
          )
        }
      >
        提交反馈
      </Button>
    </Space>
  );
}

function ExecutionSpecTab({ goalId }: { goalId: string }) {
  const spec = useExecutionSpec(goalId);
  const [feedback, setFeedback] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const [prevContent, setPrevContent] = useState<string>("");

  const contentText = (s: unknown) => (typeof s === "string" ? s : JSON.stringify(s, null, 2));

  if (spec.isLoading) return <Skeleton active />;

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space wrap>
        <Button
          loading={spec.generate.isPending}
          onClick={() => spec.generate.mutate(undefined, { onSuccess: () => message.success("已生成草稿") })}
        >
          生成执行规范草稿
        </Button>
        <Button
          type="primary"
          disabled={!spec.data?.spec}
          loading={spec.confirm.isPending}
          onClick={() => spec.confirm.mutate(undefined, { onSuccess: () => message.success("已确认") })}
        >
          确认执行规范
        </Button>
        <Button
          disabled={!spec.data?.spec}
          loading={spec.closeCheck.isPending}
          onClick={() =>
            spec.closeCheck.mutate(undefined, { onSuccess: (res) => message.info(JSON.stringify(res.outcome)) })
          }
        >
          收尾检查
        </Button>
      </Space>

      {!spec.data?.spec ? (
        <Empty description="尚未生成执行规范草稿" />
      ) : (
        <>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="版本">{spec.data.spec.version ?? "-"}</Descriptions.Item>
            <Descriptions.Item label="是否已确认">{spec.data.spec.confirmed ? "是" : "否"}</Descriptions.Item>
          </Descriptions>
          <JsonBlock data={spec.data.spec.content ?? spec.data.spec} />

          <Typography.Text strong>补充意见重新生成（修订）</Typography.Text>
          <Input.TextArea rows={2} placeholder="输入修订意见" value={feedback} onChange={(e) => setFeedback(e.target.value)} />
          <Space>
            <Button
              disabled={!feedback.trim()}
              loading={spec.revise.isPending}
              onClick={() => {
                setPrevContent(contentText(spec.data?.spec?.content ?? spec.data?.spec));
                spec.revise.mutate(
                  { feedback },
                  {
                    onSuccess: () => {
                      message.success("已生成修订版");
                      setShowDiff(true);
                      setFeedback("");
                    },
                  }
                );
              }}
            >
              提交修订
            </Button>
            {showDiff && <Button onClick={() => setShowDiff(false)}>隐藏对比</Button>}
          </Space>

          {showDiff && spec.data?.spec && (
            <DiffView oldText={prevContent} newText={contentText(spec.data.spec.content ?? spec.data.spec)} />
          )}
        </>
      )}
    </Space>
  );
}

function TuningProposalsTab({ goalId }: { goalId: string }) {
  const tp = useTuningProposals(goalId);
  if (tp.isLoading) return <Skeleton active />;
  const proposals = tp.data?.proposals || [];

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Button loading={tp.suggest.isPending} onClick={() => tp.suggest.mutate()}>
        AI 建议生成调优草案
      </Button>
      {proposals.length === 0 ? (
        <Empty description="暂无调优草案" />
      ) : (
        <List
          dataSource={proposals}
          renderItem={(p) => (
            <List.Item
              actions={[
                <Button key="c" size="small" onClick={() => tp.confirm.mutate(p.id)} loading={tp.confirm.isPending}>
                  确认
                </Button>,
                <Button key="a" size="small" type="primary" onClick={() => tp.apply.mutate(p.id)} loading={tp.apply.isPending}>
                  应用
                </Button>,
                <Button key="r" size="small" danger onClick={() => tp.reject.mutate(p.id)} loading={tp.reject.isPending}>
                  驳回
                </Button>,
              ]}
            >
              <Space direction="vertical" size={0}>
                <Tag>{p.status || "pending"}</Tag>
                <Typography.Text>{p.summary || p.id}</Typography.Text>
              </Space>
            </List.Item>
          )}
        />
      )}
    </Space>
  );
}

function CycleDiagnosticsTab({ goalId }: { goalId: string }) {
  const diag = useCycleDiagnostics(goalId);
  if (diag.isLoading) return <Skeleton active />;
  if (diag.isError) return <Alert type="warning" showIcon message="诊断信息暂不可用" />;
  return <JsonBlock data={diag.data} />;
}

function ObjectivesTab({ objectives }: { objectives: ObjectiveNode[] }) {
  const actions = useObjectiveActions();
  if (objectives.length === 0) return <Empty description="暂无关联的执行（Objective）" />;

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {objectives.map((obj) => (
        <div key={obj.id} style={{ border: "1px solid #f0f0f0", borderRadius: 8, padding: 12 }}>
          <Space style={{ marginBottom: 8 }}>
            <Tag color="blue">{obj.status}</Tag>
            <Typography.Text code>{obj.id}</Typography.Text>
          </Space>
          <Space wrap style={{ marginBottom: 8 }}>
            <Button size="small" onClick={() => actions.pause.mutate(obj.id)} loading={actions.pause.isPending}>
              暂停
            </Button>
            <Button size="small" onClick={() => actions.resume.mutate(obj.id)} loading={actions.resume.isPending}>
              恢复
            </Button>
            <Button size="small" onClick={() => actions.retry.mutate(obj.id)} loading={actions.retry.isPending}>
              重试
            </Button>
            <Popconfirm title="确认取消该执行？" onConfirm={() => actions.cancel.mutate(obj.id)}>
              <Button size="small" danger>
                取消
              </Button>
            </Popconfirm>
          </Space>
          {obj.steps && obj.steps.length > 0 && (
            <Steps
              direction="vertical"
              size="small"
              items={obj.steps.map((s, idx) => ({
                title: s.title || `步骤 ${idx + 1}`,
                description: (
                  <Space direction="vertical" size={0}>
                    <Tag>{s.status || "pending"}</Tag>
                    <Button
                      size="small"
                      type="link"
                      onClick={() => actions.resetStep.mutate({ id: obj.id, idx })}
                      loading={actions.resetStep.isPending}
                    >
                      重置该步骤
                    </Button>
                  </Space>
                ),
              }))}
            />
          )}
        </div>
      ))}
    </Space>
  );
}

export default function GoalDetailDrawer({
  goalId,
  goal,
  objectives,
  onClose,
}: {
  goalId: string | undefined;
  goal: GoalNode | undefined;
  objectives: ObjectiveNode[];
  onClose: () => void;
}) {
  return (
    <Drawer title={goal ? goal.title : goalId} open={!!goalId} width={640} onClose={onClose} destroyOnClose>
      {!goal || !goalId ? (
        <Empty description="未找到目标" />
      ) : (
        <Tabs
          items={[
            { key: "overview", label: "概览", children: <OverviewTab goal={goal} /> },
            { key: "spec", label: "执行规范", children: <ExecutionSpecTab goalId={goalId} /> },
            { key: "objectives", label: `执行详情（${objectives.length}）`, children: <ObjectivesTab objectives={objectives} /> },
            { key: "tuning", label: "调优草案", children: <TuningProposalsTab goalId={goalId} /> },
            { key: "diagnostics", label: "周期诊断", children: <CycleDiagnosticsTab goalId={goalId} /> },
          ]}
        />
      )}
    </Drawer>
  );
}
