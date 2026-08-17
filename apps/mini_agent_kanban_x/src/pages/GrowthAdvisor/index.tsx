import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Space,
  Statistic,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  BulbOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  useGrowthActions,
  useGrowthAlign,
  useGrowthCandidateTimeline,
  useGrowthFollowups,
  useGrowthMaterialBody,
  useGrowthPortfolioSummary,
  useGrowthPursuits,
  useGrowthReportBody,
  useGrowthSummary,
} from "../../hooks/useGrowth";
import type { GrowthCandidate } from "../../api/types";

const { Text, Paragraph, Title } = Typography;

const STATUS_COLOR: Record<string, string> = {
  pending: "blue",
  accepted: "green",
  dismissed: "default",
};

export default function GrowthAdvisor() {
  const { data: summary, isLoading } = useGrowthSummary();
  const { data: followups } = useGrowthFollowups();
  const { data: pursuits } = useGrowthPursuits();
  const [alignOpen, setAlignOpen] = useState(false);
  const { data: align, isLoading: alignLoading } = useGrowthAlign(alignOpen);
  const { data: portfolio } = useGrowthPortfolioSummary(alignOpen);
  const actions = useGrowthActions();

  const [detailCandidate, setDetailCandidate] = useState<GrowthCandidate | null>(null);
  const [reportId, setReportId] = useState<string | undefined>(undefined);
  const [materialId, setMaterialId] = useState<string | undefined>(undefined);
  const [dismissTarget, setDismissTarget] = useState<GrowthCandidate | null>(null);
  const [dismissReason, setDismissReason] = useState("");
  const [newTopic, setNewTopic] = useState("");
  const [newKeywords, setNewKeywords] = useState("");

  const { data: timeline } = useGrowthCandidateTimeline(detailCandidate?.candidate_id);
  const { data: reportBody } = useGrowthReportBody(reportId);
  const { data: materialBody } = useGrowthMaterialBody(materialId);

  const candidates = summary?.candidates || [];
  const diag = summary?.diagnostics || {};
  const pendingCount = candidates.filter((c) => c.status === "pending").length;

  const runAction = (candidateId: string, action: "accept" | "dismiss", reason?: string) => {
    actions.candidateAction.mutate(
      { candidateId, action, reason },
      { onSuccess: () => message.success(action === "accept" ? "已采纳" : "已忽略") }
    );
  };

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Title level={4} style={{ margin: 0 }}>
          🌱 成长顾问
        </Title>
        <Button icon={<SearchOutlined />} loading={actions.scan.isPending} onClick={() => actions.scan.mutate()}>
          立即为我看看
        </Button>
        <Button onClick={() => setAlignOpen(true)}>对齐视图</Button>
      </Space>

      {summary?.first_touch_notice_shown === false && (
        <Alert
          type="info"
          showIcon
          closable
          style={{ marginBottom: 16 }}
          message="成长顾问会基于你的记忆与对话，主动发现你可能感兴趣的新方向，仅供参考，采纳与否由你决定。"
          onClose={() => actions.ackFirstTouch.mutate()}
        />
      )}

      <Space size="large" style={{ marginBottom: 16 }} wrap>
        <Statistic title="待处理候选" value={pendingCount} />
        <Statistic title="调研报告" value={summary?.reports?.length || 0} />
        <Statistic title="正在自主推进" value={pursuits?.pursuits?.length || 0} />
        <Statistic title="待回访方向" value={followups?.followups?.length || 0} />
      </Space>

      <Collapse
        style={{ marginBottom: 16 }}
        items={[
          {
            key: "diag",
            label: "诊断信息（为什么候选是 0？）",
            children: (
              <Descriptions size="small" column={2} bordered>
                {Object.entries(diag)
                  .filter(([k]) => k !== "cron_jobs")
                  .map(([k, v]) => (
                    <Descriptions.Item key={k} label={k}>
                      {typeof v === "object" ? JSON.stringify(v) : String(v)}
                    </Descriptions.Item>
                  ))}
              </Descriptions>
            ),
          },
        ]}
      />

      <Tabs
        items={[
          {
            key: "candidates",
            label: `候选列表 (${candidates.length})`,
            children: (
              <List
                loading={isLoading}
                dataSource={candidates}
                locale={{ emptyText: <Empty description="暂无候选，试试点击「立即为我看看」" /> }}
                renderItem={(c) => (
                  <List.Item
                    actions={[
                      <a key="detail" onClick={() => setDetailCandidate(c)}>
                        详情
                      </a>,
                      c.status === "pending" && (
                        <a key="accept" onClick={() => runAction(c.candidate_id, "accept")}>
                          采纳
                        </a>
                      ),
                      c.status === "pending" && (
                        <a
                          key="dismiss"
                          onClick={() => {
                            setDismissTarget(c);
                            setDismissReason("");
                          }}
                        >
                          忽略
                        </a>
                      ),
                      c.report_id && (
                        <a key="report" onClick={() => setReportId(c.report_id)}>
                          查看报告
                        </a>
                      ),
                      <a key="refresh" onClick={() => actions.refreshReport.mutate(c.candidate_id)}>
                        刷新报告
                      </a>,
                      <a key="material" onClick={() => actions.generateMaterial.mutate(c.candidate_id, { onSuccess: (r: any) => setMaterialId(r?.material?.material_id) })}>
                        生成素材
                      </a>,
                      !c.linked_goal_id && c.report_id && (
                        <a key="adopt" onClick={() => actions.adoptGoal.mutate(c.candidate_id)}>
                          落地为目标
                        </a>
                      ),
                    ].filter(Boolean)}
                  >
                    <List.Item.Meta
                      title={
                        <Space>
                          {c.title}
                          <Tag color={STATUS_COLOR[c.status || "pending"] || "default"}>{c.status || "pending"}</Tag>
                        </Space>
                      }
                      description={typeof c.score === "number" ? `匹配度: ${c.score}` : undefined}
                    />
                  </List.Item>
                )}
              />
            ),
          },
          {
            key: "keywords",
            label: "关键词管理",
            children: (
              <div>
                <Space style={{ marginBottom: 12 }}>
                  <Input placeholder="主题" value={newTopic} onChange={(e) => setNewTopic(e.target.value)} style={{ width: 160 }} />
                  <Input
                    placeholder="关键词（逗号分隔）"
                    value={newKeywords}
                    onChange={(e) => setNewKeywords(e.target.value)}
                    style={{ width: 240 }}
                  />
                  <Button
                    type="primary"
                    disabled={!newTopic}
                    loading={actions.addKeyword.isPending}
                    onClick={() =>
                      actions.addKeyword.mutate(
                        { topic: newTopic, keywords: newKeywords },
                        {
                          onSuccess: () => {
                            setNewTopic("");
                            setNewKeywords("");
                            message.success("已添加");
                          },
                        }
                      )
                    }
                  >
                    新增
                  </Button>
                </Space>
                <Paragraph type="secondary">
                  内置/已学到的主题关键词管理请结合诊断信息里的画像展开；确认/删除/恢复可对已知主题名调用下方操作：
                </Paragraph>
                <Space wrap>
                  <TopicQuickActions actions={actions} />
                </Space>
              </div>
            ),
          },
          {
            key: "followups",
            label: `回访提醒 (${followups?.followups?.length || 0})`,
            children: (
              <List
                dataSource={followups?.followups || []}
                locale={{ emptyText: "暂无待回访方向" }}
                renderItem={(f) => (
                  <List.Item
                    actions={[
                      <a key="p" onClick={() => actions.followup.mutate({ candidateId: f.candidate_id, outcome: "progressed" })}>
                        进展顺利
                      </a>,
                      <a key="s" onClick={() => actions.followup.mutate({ candidateId: f.candidate_id, outcome: "stalled" })}>
                        暂无进展
                      </a>,
                    ]}
                  >
                    <List.Item.Meta title={f.title} description={f.question_hint} />
                  </List.Item>
                )}
              />
            ),
          },
          {
            key: "pursuits",
            label: `追求中方向 (${pursuits?.pursuits?.length || 0})`,
            children: (
              <List
                dataSource={pursuits?.pursuits || []}
                locale={{ emptyText: "暂无正在自主推进的方向" }}
                renderItem={(p) => (
                  <List.Item
                    actions={[
                      <a key="view" onClick={() => actions.viewPursuitMaterial.mutate(p.goal_id)}>
                        查看素材
                      </a>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Space>
                          {p.title}
                          {p.recurring && <Tag color="processing">周期性</Tag>}
                          {p.pursuit_style && <Tag>{p.pursuit_style}</Tag>}
                        </Space>
                      }
                      description={`关联目标: ${p.goal_title || p.goal_id} · 已跑 ${p.cycle_count ?? 0} 轮 · 下次执行: ${
                        p.next_run_at || "-"
                      }`}
                    />
                  </List.Item>
                )}
              />
            ),
          },
          {
            key: "reports",
            label: `调研报告 (${summary?.reports?.length || 0})`,
            children: (
              <List
                dataSource={summary?.reports || []}
                locale={{ emptyText: "暂无报告" }}
                renderItem={(r) => (
                  <List.Item actions={[<a key="v" onClick={() => setReportId(r.report_id)}>查看</a>]}>
                    <List.Item.Meta title={r.title || r.report_id} description={r.created_at} />
                  </List.Item>
                )}
              />
            ),
          },
        ]}
      />

      <Drawer title="候选详情" open={!!detailCandidate} onClose={() => setDetailCandidate(null)} width={520}>
        {detailCandidate && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="标题">{detailCandidate.title}</Descriptions.Item>
              <Descriptions.Item label="状态">{detailCandidate.status}</Descriptions.Item>
              <Descriptions.Item label="报告">{detailCandidate.report_id || "-"}</Descriptions.Item>
            </Descriptions>
            <Title level={5}>主题时间线</Title>
            <List
              size="small"
              dataSource={(timeline?.events as any[]) || []}
              locale={{ emptyText: "暂无记录" }}
              renderItem={(e: any) => (
                <List.Item>
                  <Text type="secondary">{e.at || e.ts}</Text> {e.event || e.title}
                </List.Item>
              )}
            />
          </>
        )}
      </Drawer>

      <Modal
        title={`查看报告 ${reportId || ""}`}
        open={!!reportId}
        onCancel={() => setReportId(undefined)}
        footer={null}
        width={720}
      >
        <Paragraph style={{ whiteSpace: "pre-wrap", maxHeight: "70vh", overflow: "auto" }}>{reportBody?.body}</Paragraph>
      </Modal>

      <Modal
        title={`学习素材 ${materialId || ""}`}
        open={!!materialId}
        onCancel={() => setMaterialId(undefined)}
        footer={null}
        width={720}
      >
        <Paragraph style={{ whiteSpace: "pre-wrap", maxHeight: "70vh", overflow: "auto" }}>{materialBody?.body}</Paragraph>
      </Modal>

      <Modal
        title="忽略该候选"
        open={!!dismissTarget}
        onCancel={() => setDismissTarget(null)}
        onOk={() => {
          if (dismissTarget) runAction(dismissTarget.candidate_id, "dismiss", dismissReason || undefined);
          setDismissTarget(null);
        }}
      >
        <Input placeholder="忽略原因（可选）" value={dismissReason} onChange={(e) => setDismissReason(e.target.value)} />
      </Modal>

      <Drawer title="对齐视图（兴趣 ⇄ 目标）" open={alignOpen} onClose={() => setAlignOpen(false)} width={560}>
        <Space style={{ marginBottom: 12 }}>
          <Button type="primary" loading={actions.alignAdoptAll.isPending} onClick={() => actions.alignAdoptAll.mutate()}>
            一键全部采纳
          </Button>
        </Space>
        {portfolio ? (
          <Alert
            type="info"
            style={{ marginBottom: 12 }}
            message="推进组合摘要"
            description={<pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(portfolio, null, 2)}</pre>}
          />
        ) : null}
        <Title level={5}>有兴趣但未建目标</Title>
        <List
          loading={alignLoading}
          dataSource={align?.unmatched_interests || []}
          locale={{ emptyText: "暂无" }}
          renderItem={(it: any) => <List.Item>{it.topic || JSON.stringify(it)}</List.Item>}
        />
        <Title level={5}>AI 建议匹配</Title>
        <List
          dataSource={align?.llm_suggested_matches || []}
          locale={{ emptyText: "暂无建议" }}
          renderItem={(m) => (
            <List.Item
              actions={[
                <a key="c" onClick={() => m.goal_id && actions.alignConfirmMatch.mutate({ topic: m.topic, goalId: m.goal_id })}>
                  确认匹配
                </a>,
              ]}
            >
              {m.topic} → {m.goal_id}
            </List.Item>
          )}
        />
      </Drawer>
    </div>
  );
}

function TopicQuickActions({ actions }: { actions: ReturnType<typeof useGrowthActions> }) {
  const [topic, setTopic] = useState("");
  return (
    <Space>
      <Input placeholder="主题名" value={topic} onChange={(e) => setTopic(e.target.value)} style={{ width: 160 }} />
      <Button size="small" disabled={!topic} onClick={() => actions.confirmKeyword.mutate(topic)}>
        ✅ 保留
      </Button>
      <Button size="small" disabled={!topic} onClick={() => actions.removeKeyword.mutate(topic)}>
        ❌ 删除/隐藏
      </Button>
      <Button size="small" disabled={!topic} icon={<ReloadOutlined />} onClick={() => actions.restoreKeyword.mutate(topic)}>
        恢复
      </Button>
      <Tag icon={<BulbOutlined />}>对内置主题用"删除"即为隐藏，可用"恢复"撤销</Tag>
    </Space>
  );
}
