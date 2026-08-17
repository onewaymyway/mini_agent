import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Skeleton,
  Space,
  Steps,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { PlayCircleOutlined } from "@ant-design/icons";
import {
  useWorkflowActions,
  useWorkflowRunDetail,
  useWorkflowRuns,
  useWorkflowStats,
  useWorkflowYaml,
  useWorkflows,
} from "../../hooks/useWorkflows";

const { TextArea } = Input;

function RunDetailPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run, isLoading } = useWorkflowRunDetail(runId);
  const actions = useWorkflowActions();
  const [rejectReason, setRejectReason] = useState("");
  const [inputText, setInputText] = useState("");
  const [overrideOutput, setOverrideOutput] = useState<Record<string, string>>({});

  if (isLoading) return <Skeleton active />;
  if (!run) return <Empty description="未找到该执行记录" />;

  const awaitingApproval = run.status === "awaiting_approval";
  const awaitingInput = run.status === "awaiting_input";

  return (
    <Card size="small" title={`执行详情：${runId}`} extra={<Button size="small" onClick={onClose}>关闭</Button>}>
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="工作流">{run.name || "-"}</Descriptions.Item>
        <Descriptions.Item label="状态"><Tag>{run.status}</Tag></Descriptions.Item>
      </Descriptions>

      <Space wrap style={{ marginBottom: 12 }}>
        <Button size="small" onClick={() => actions.pause.mutate(runId)} loading={actions.pause.isPending}>暂停</Button>
        <Button size="small" onClick={() => actions.resume.mutate({ runId })} loading={actions.resume.isPending}>恢复</Button>
        <Popconfirm title="确认取消该执行？" onConfirm={() => actions.cancel.mutate(runId)}>
          <Button size="small" danger>取消</Button>
        </Popconfirm>
        <Button size="small" onClick={() => actions.markInterrupted.mutate(runId, { onSuccess: () => message.success("已标记为中断") })}>
          标记为中断（孤儿修复）
        </Button>
      </Space>

      {awaitingApproval && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="该执行正等待审批"
          description={
            <Space>
              <Button size="small" type="primary" onClick={() => actions.approve.mutate(runId)} loading={actions.approve.isPending}>通过</Button>
              <Input size="small" placeholder="驳回理由" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} style={{ width: 200 }} />
              <Button size="small" danger onClick={() => actions.reject.mutate({ runId, reason: rejectReason })} loading={actions.reject.isPending}>驳回</Button>
            </Space>
          }
        />
      )}

      {awaitingInput && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="该执行正等待人工输入"
          description={
            <Space.Compact style={{ width: "100%" }}>
              <Input value={inputText} onChange={(e) => setInputText(e.target.value)} placeholder="输入内容" />
              <Button type="primary" onClick={() => actions.provideInput.mutate({ runId, text: inputText })} loading={actions.provideInput.isPending}>提交</Button>
            </Space.Compact>
          }
        />
      )}

      {run.step_results && run.step_results.length > 0 ? (
        <Steps
          direction="vertical"
          size="small"
          items={run.step_results.map((s, idx) => {
            const stepId = s.step_id || String(idx);
            return {
              title: stepId,
              description: (
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Tag>{s.status || "pending"}</Tag>
                  {s.output && <pre style={{ fontSize: 12, background: "#fafafa", padding: 8, maxHeight: 160, overflow: "auto" }}>{s.output}</pre>}
                  <Space.Compact style={{ width: "100%" }}>
                    <TextArea
                      autoSize={{ minRows: 1, maxRows: 3 }}
                      placeholder="编辑该步骤输出（用于单步编辑续跑）"
                      value={overrideOutput[stepId] ?? ""}
                      onChange={(e) => setOverrideOutput((prev) => ({ ...prev, [stepId]: e.target.value }))}
                    />
                    <Button
                      onClick={() =>
                        actions.overrideStep.mutate(
                          { runId, stepId, output: overrideOutput[stepId] || "" },
                          { onSuccess: () => message.success("已覆盖，可选择从此步骤续跑") }
                        )
                      }
                    >
                      保存覆盖
                    </Button>
                    <Button
                      onClick={() =>
                        actions.resume.mutate(
                          { runId, body: { force_rerun_from: stepId } },
                          { onSuccess: () => message.success("已触发从该步骤续跑") }
                        )
                      }
                    >
                      从此续跑
                    </Button>
                  </Space.Compact>
                </Space>
              ),
            };
          })}
        />
      ) : (
        <Empty description="暂无步骤结果" />
      )}
    </Card>
  );
}

/** 工作流页面：对应旧看板 Tab4，含定义查看、运行面板、历史统计、执行详情与运行控制。 */
export default function Workflows() {
  const { data, isLoading, isError, error } = useWorkflows();
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [inputsJson, setInputsJson] = useState("{}");
  const [background, setBackground] = useState(true);
  const [activeRunId, setActiveRunId] = useState<string | undefined>(undefined);

  const yamlQuery = useWorkflowYaml(selected);
  const statsQuery = useWorkflowStats(selected);
  const runsQuery = useWorkflowRuns(selected);
  const actions = useWorkflowActions();

  return (
    <Row gutter={16}>
      <Col span={7}>
        <Card title="工作流定义" size="small">
          {isError && <Alert type="error" showIcon message={(error as Error).message} />}
          <List
            loading={isLoading}
            dataSource={data?.workflows || []}
            locale={{ emptyText: <Empty description="暂无工作流" /> }}
            renderItem={(w) => (
              <List.Item onClick={() => setSelected(w.name)} style={{ cursor: "pointer" }}>
                <Typography.Text strong={selected === w.name}>{w.name}</Typography.Text>
              </List.Item>
            )}
          />
        </Card>
      </Col>
      <Col span={17}>
        {!selected ? (
          <Empty description="从左侧选择一个工作流" />
        ) : (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Card
              size="small"
              title={selected}
              extra={
                <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => setRunModalOpen(true)}>
                  运行
                </Button>
              }
            >
              <Tabs
                items={[
                  {
                    key: "yaml",
                    label: "YAML 定义",
                    children: yamlQuery.isLoading ? (
                      <Skeleton active />
                    ) : (
                      <pre style={{ maxHeight: 360, overflow: "auto", background: "#fafafa", padding: 12 }}>{yamlQuery.data?.yaml}</pre>
                    ),
                  },
                  {
                    key: "stats",
                    label: "历史统计",
                    children: statsQuery.isLoading ? (
                      <Skeleton active />
                    ) : (
                      <pre style={{ maxHeight: 360, overflow: "auto", background: "#fafafa", padding: 12 }}>{JSON.stringify(statsQuery.data, null, 2)}</pre>
                    ),
                  },
                  {
                    key: "runs",
                    label: `执行历史（${runsQuery.data?.runs?.length ?? 0}）`,
                    children: (
                      <List
                        loading={runsQuery.isLoading}
                        dataSource={runsQuery.data?.runs || []}
                        locale={{ emptyText: <Empty description="暂无执行记录" /> }}
                        renderItem={(r) => (
                          <List.Item actions={[<Button key="v" size="small" onClick={() => setActiveRunId(r.workflow_session_id)}>查看详情</Button>]}>
                            <Space direction="vertical" size={0}>
                              <Tag>{r.status}</Tag>
                              <Typography.Text code>{r.workflow_session_id}</Typography.Text>
                            </Space>
                          </List.Item>
                        )}
                      />
                    ),
                  },
                ]}
              />
            </Card>

            {activeRunId && <RunDetailPanel runId={activeRunId} onClose={() => setActiveRunId(undefined)} />}
          </Space>
        )}
      </Col>

      <Modal
        title={`运行工作流：${selected}`}
        open={runModalOpen}
        onCancel={() => setRunModalOpen(false)}
        onOk={() => {
          let inputs: unknown = {};
          try {
            inputs = JSON.parse(inputsJson || "{}");
          } catch {
            message.error("输入参数不是合法 JSON");
            return;
          }
          actions.run.mutate(
            { name: selected as string, body: { inputs, background } },
            {
              onSuccess: (res) => {
                message.success("已发起运行");
                setRunModalOpen(false);
                const sid = (res as { workflow_session_id?: string }).workflow_session_id;
                if (sid) setActiveRunId(sid);
              },
            }
          );
        }}
        confirmLoading={actions.run.isPending}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text>输入参数（JSON）</Typography.Text>
          <TextArea rows={6} value={inputsJson} onChange={(e) => setInputsJson(e.target.value)} style={{ fontFamily: "monospace" }} />
          <Space>
            <Switch checked={background} onChange={setBackground} />
            <Typography.Text>后台运行（含审批步骤时会强制后台）</Typography.Text>
          </Space>
          <Button
            onClick={() => {
              let inputs: unknown = {};
              try {
                inputs = JSON.parse(inputsJson || "{}");
              } catch {
                message.error("输入参数不是合法 JSON");
                return;
              }
              actions.preview.mutate(
                { name: selected as string, inputs },
                { onSuccess: (res) => Modal.info({ title: "预览结果（dry-run）", content: <pre>{JSON.stringify(res, null, 2)}</pre>, width: 600 }) }
              );
            }}
            loading={actions.preview.isPending}
          >
            预览（不实际执行）
          </Button>
        </Space>
      </Modal>
    </Row>
  );
}
