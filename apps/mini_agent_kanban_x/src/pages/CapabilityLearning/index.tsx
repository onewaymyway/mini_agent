import { useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import {
  useCapabilityActions,
  useCapabilityPersonaDraft,
  useCapabilityPersonas,
  useCapabilityQuestions,
  useCapabilitySuggestions,
  useCapabilityTrack,
  useCapabilityTrackLedger,
  useCapabilityTracks,
} from "../../hooks/useCapability";

const { Text, Paragraph, Title } = Typography;

export default function CapabilityLearning() {
  const { data: tracks, isLoading } = useCapabilityTracks();
  const { data: questions } = useCapabilityQuestions();
  const { data: suggestions } = useCapabilitySuggestions();
  const { data: personas } = useCapabilityPersonas();
  const actions = useCapabilityActions();

  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [selectedTrackId, setSelectedTrackId] = useState<string | undefined>(undefined);
  const [answerTarget, setAnswerTarget] = useState<{ question_id: string } | null>(null);
  const [answerText, setAnswerText] = useState("");

  const { data: trackDetail } = useCapabilityTrack(selectedTrackId);
  const { data: ledger } = useCapabilityTrackLedger(selectedTrackId);
  const { data: personaDraft } = useCapabilityPersonaDraft(
    trackDetail?.target_type === "persona" ? selectedTrackId : undefined
  );

  const pendingQuestions = (questions?.questions || []).filter((q) => q.status === "pending" || !q.status);
  const historyQuestions = (questions?.questions || []).filter((q) => q.status && q.status !== "pending");
  const pendingSuggestions = (suggestions?.suggestions || []).filter((s) => s.status === "pending" || !s.status);

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>
          🎓 能力学习 / 人设养成
        </Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建方向
        </Button>
      </Space>

      <Tabs
        items={[
          {
            key: "tracks",
            label: `方向列表 (${tracks?.tracks?.length || 0})`,
            children: (
              <List
                loading={isLoading}
                dataSource={tracks?.tracks || []}
                locale={{ emptyText: <Empty description="暂无方向，点击「新建方向」开始" /> }}
                renderItem={(t) => (
                  <List.Item
                    actions={[
                      <a key="detail" onClick={() => setSelectedTrackId(t.track_id)}>
                        详情
                      </a>,
                      <a
                        key="del"
                        onClick={() =>
                          Modal.confirm({
                            title: `删除方向「${t.title}」？`,
                            content: "不会级联删除已产出的 wiki 页面。",
                            onOk: () => actions.deleteTrack.mutate(t.track_id),
                          })
                        }
                      >
                        删除
                      </a>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Space>
                          {t.title}
                          <Tag>{t.target_type}</Tag>
                          <Tag color={t.status === "active" ? "green" : "default"}>{t.status || "active"}</Tag>
                        </Space>
                      }
                      description={t.persona_desc}
                    />
                  </List.Item>
                )}
              />
            ),
          },
          {
            key: "questions",
            label: `待回答问题 (${pendingQuestions.length})`,
            children: (
              <>
                <List
                  dataSource={pendingQuestions}
                  locale={{ emptyText: "暂无待回答问题" }}
                  renderItem={(q) => (
                    <List.Item
                      actions={[
                        <a
                          key="a"
                          onClick={() => {
                            setAnswerTarget({ question_id: q.question_id });
                            setAnswerText("");
                          }}
                        >
                          回答
                        </a>,
                        <a key="d" onClick={() => actions.dismissQuestion.mutate(q.question_id)}>
                          忽略
                        </a>,
                      ]}
                    >
                      <List.Item.Meta title={q.question} description={q.track_id} />
                    </List.Item>
                  )}
                />
                <Title level={5} style={{ marginTop: 24 }}>
                  历史问答（已回答/已忽略/已过期）
                </Title>
                <List
                  size="small"
                  dataSource={historyQuestions}
                  locale={{ emptyText: "暂无历史" }}
                  renderItem={(q) => (
                    <List.Item>
                      <List.Item.Meta
                        title={
                          <Space>
                            {q.question}
                            <Tag>{q.status}</Tag>
                          </Space>
                        }
                        description={q.answer}
                      />
                    </List.Item>
                  )}
                />
              </>
            ),
          },
          {
            key: "suggestions",
            label: `大纲扩展建议 (${pendingSuggestions.length})`,
            children: (
              <List
                dataSource={pendingSuggestions}
                locale={{ emptyText: "暂无建议" }}
                renderItem={(s) => (
                  <List.Item
                    actions={[
                      <a key="accept" onClick={() => actions.acceptSuggestion.mutate(s.suggestion_id)}>
                        采纳
                      </a>,
                      <a key="dismiss" onClick={() => actions.dismissSuggestion.mutate(s.suggestion_id)}>
                        忽略
                      </a>,
                    ]}
                  >
                    <List.Item.Meta title={s.topic} description={s.reason} />
                  </List.Item>
                )}
              />
            ),
          },
          {
            key: "personas",
            label: `已发布角色 (${personas?.personas?.length || 0})`,
            children: (
              <List
                dataSource={personas?.personas || []}
                locale={{ emptyText: "暂无已发布角色" }}
                renderItem={(p) => <PersonaRow persona={p} actions={actions} />}
              />
            ),
          },
        ]}
      />

      <Modal title="新建能力/人设方向" open={createOpen} onCancel={() => setCreateOpen(false)} footer={null}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ target_type: "knowledge", llm_draft: false }}
          onFinish={(values) => {
            actions.createTrack.mutate(
              {
                ...values,
                outline_names: values.outline_names
                  ? String(values.outline_names).split(/[,，\n]/).map((s: string) => s.trim()).filter(Boolean)
                  : undefined,
              },
              {
                onSuccess: () => {
                  message.success("已创建");
                  setCreateOpen(false);
                  form.resetFields();
                },
              }
            );
          }}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="persona_desc" label="方向描述" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="target_type" label="目标类型">
            <Radio.Group options={[{ label: "知识型 knowledge", value: "knowledge" }, { label: "人设型 persona", value: "persona" }]} />
          </Form.Item>
          <Form.Item name="wiki_tag" label="Wiki 标签（可选）">
            <Input />
          </Form.Item>
          <Form.Item name="outline_names" label="初始大纲子主题（逗号分隔，可留空）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="llm_draft" label="留空时用 LLM 起草初始大纲" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={actions.createTrack.isPending}>
            创建
          </Button>
        </Form>
      </Modal>

      <Modal
        title="回答问题"
        open={!!answerTarget}
        onCancel={() => setAnswerTarget(null)}
        onOk={() => {
          if (answerTarget) actions.answerQuestion.mutate({ questionId: answerTarget.question_id, answer: answerText });
          setAnswerTarget(null);
        }}
      >
        <Input.TextArea rows={4} value={answerText} onChange={(e) => setAnswerText(e.target.value)} />
      </Modal>

      <Drawer title="方向详情" open={!!selectedTrackId} onClose={() => setSelectedTrackId(undefined)} width={560}>
        {trackDetail && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="标题">{trackDetail.title}</Descriptions.Item>
              <Descriptions.Item label="描述">{trackDetail.persona_desc}</Descriptions.Item>
              <Descriptions.Item label="类型">{trackDetail.target_type}</Descriptions.Item>
              <Descriptions.Item label="状态">{trackDetail.status}</Descriptions.Item>
              <Descriptions.Item label="Wiki 标签">{trackDetail.wiki_tag || "-"}</Descriptions.Item>
              <Descriptions.Item label="节奏 cadence">{trackDetail.cadence || "-"}</Descriptions.Item>
            </Descriptions>

            <Tabs
              items={[
                {
                  key: "outline",
                  label: "大纲",
                  children: (
                    <List
                      size="small"
                      dataSource={trackDetail.outline || []}
                      locale={{ emptyText: "暂无大纲子主题" }}
                      renderItem={(o: any) => <List.Item>{o.name}</List.Item>}
                    />
                  ),
                },
                {
                  key: "ledger",
                  label: "学习台账",
                  children: (
                    <List
                      size="small"
                      dataSource={ledger?.entries || []}
                      locale={{ emptyText: "暂无记录" }}
                      renderItem={(e: any) => (
                        <List.Item>
                          <Text type="secondary">{e.created_at}</Text> {e.summary}
                        </List.Item>
                      )}
                    />
                  ),
                },
                ...(trackDetail.target_type === "persona"
                  ? [
                      {
                        key: "persona",
                        label: "人设草稿",
                        children: (
                          <div>
                            <Space style={{ marginBottom: 12 }}>
                              <Button
                                loading={actions.draftPersona.isPending}
                                onClick={() => actions.draftPersona.mutate(selectedTrackId as string)}
                              >
                                生成/刷新草稿
                              </Button>
                              <Button
                                type="primary"
                                loading={actions.publishPersona.isPending}
                                onClick={() =>
                                  actions.publishPersona.mutate(selectedTrackId as string, {
                                    onSuccess: () => message.success("已发布"),
                                    onError: (e: any) => message.error(e?.message || "发布失败"),
                                  })
                                }
                              >
                                发布
                              </Button>
                            </Space>
                            {personaDraft ? (
                              <>
                                <Paragraph type="secondary">
                                  完成度：{JSON.stringify(personaDraft.completeness)}
                                </Paragraph>
                                <Paragraph style={{ whiteSpace: "pre-wrap" }}>{personaDraft.draft}</Paragraph>
                              </>
                            ) : (
                              <Empty description="尚未生成草稿" />
                            )}
                          </div>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          </>
        )}
      </Drawer>
    </div>
  );
}

function PersonaRow({ persona, actions }: { persona: any; actions: ReturnType<typeof useCapabilityActions> }) {
  const [scopes, setScopes] = useState<string[]>(persona.wiki_scopes || []);
  return (
    <List.Item
      actions={[
        <Button
          key="save"
          size="small"
          loading={actions.setPersonaWikiScopes.isPending}
          onClick={() => actions.setPersonaWikiScopes.mutate({ personaName: persona.name, wikiScopes: scopes })}
        >
          保存知识范围
        </Button>,
      ]}
    >
      <List.Item.Meta
        title={persona.display_name || persona.name}
        description={
          <Select
            mode="tags"
            style={{ minWidth: 260 }}
            value={scopes}
            onChange={setScopes}
            placeholder="wiki_scopes（标签）"
          />
        }
      />
    </List.Item>
  );
}
