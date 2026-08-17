import { useState } from "react";
import { Alert, Button, Card, Empty, List, Modal, Space, Tag, Typography, message } from "antd";
import { useEvolutionActions, useEvolutionProposalDiff, useEvolutionProposals } from "../../hooks/useEvolution";

const { Title, Text } = Typography;

const RISK_COLOR: Record<string, string> = { low: "green", medium: "orange", high: "red" };

function DiffText({ text }: { text: string }) {
  if (!text) return <Empty description="无内容" />;
  return (
    <pre
      style={{
        fontFamily: "monospace",
        fontSize: 12,
        lineHeight: 1.6,
        background: "#fafafa",
        border: "1px solid #f0f0f0",
        borderRadius: 6,
        padding: 12,
        maxHeight: 520,
        overflow: "auto",
        margin: 0,
      }}
    >
      {text.split("\n").map((line, i) => {
        const color = line.startsWith("+") && !line.startsWith("+++") ? "#e6ffed" : line.startsWith("-") && !line.startsWith("---") ? "#ffeef0" : undefined;
        const textColor = line.startsWith("+") && !line.startsWith("+++") ? "#22863a" : line.startsWith("-") && !line.startsWith("---") ? "#b31d28" : undefined;
        return (
          <div key={i} style={{ background: color, color: textColor, whiteSpace: "pre-wrap" }}>
            {line}
          </div>
        );
      })}
    </pre>
  );
}

export default function EvolutionProposals() {
  const { data, isLoading } = useEvolutionProposals();
  const actions = useEvolutionActions();
  const [diffBranch, setDiffBranch] = useState<string | undefined>(undefined);
  const [confirmForce, setConfirmForce] = useState<{ branch: string } | null>(null);
  const { data: diff, isLoading: diffLoading } = useEvolutionProposalDiff(diffBranch);

  const items = data?.items || [];
  // 按文件分组展示：从 diff 文本里按 "diff --git" 切分（单文件时默认展开，由渲染顺序体现）
  const grouped = diff?.diff
    ? diff.diff.split(/(?=^diff --git )/m).filter(Boolean)
    : [];

  return (
    <Card title="🧬 进化提案">
      <List
        loading={isLoading}
        dataSource={items}
        locale={{ emptyText: <Empty description="暂无待处理的进化提案" /> }}
        renderItem={(p) => (
          <List.Item
            actions={[
              <a key="diff" onClick={() => setDiffBranch(p.branch)}>
                查看 diff
              </a>,
              p.risk === "low" ? (
                <a
                  key="merge"
                  onClick={() =>
                    actions.merge.mutate(
                      { branch: p.branch },
                      { onSuccess: () => message.success("已合并"), onError: (e: any) => message.error(e?.message || "合并失败") }
                    )
                  }
                >
                  一键合并
                </a>
              ) : (
                <a key="merge-force" onClick={() => setConfirmForce({ branch: p.branch })}>
                  强制合并
                </a>
              ),
            ]}
          >
            <List.Item.Meta
              title={
                <Space>
                  <Text code>{p.branch}</Text>
                  <Tag color={RISK_COLOR[p.risk || ""] || "default"}>{p.risk || "unknown"}</Tag>
                </Space>
              }
              description={Array.isArray(p.reasons) ? p.reasons.join("；") : undefined}
            />
          </List.Item>
        )}
      />

      <Modal title={`提案 diff：${diffBranch || ""}`} open={!!diffBranch} onCancel={() => setDiffBranch(undefined)} footer={null} width={860}>
        {diffLoading ? (
          "加载中…"
        ) : grouped.length > 1 ? (
          grouped.map((chunk, i) => <DiffText key={i} text={chunk} />)
        ) : (
          <DiffText text={diff?.diff || ""} />
        )}
      </Modal>

      <Modal
        title="强制合并确认"
        open={!!confirmForce}
        onCancel={() => setConfirmForce(null)}
        onOk={() => {
          if (confirmForce) {
            actions.merge.mutate(
              { branch: confirmForce.branch, force: true },
              {
                onSuccess: () => message.success("已强制合并"),
                onError: (e: any) => message.error(e?.message || "合并失败"),
              }
            );
          }
          setConfirmForce(null);
        }}
        okButtonProps={{ danger: true }}
      >
        <Alert type="warning" showIcon message="该提案风险等级不是 low，强制合并会跳过风险拦截，请确认已经人工审查过 diff。" />
      </Modal>
    </Card>
  );
}
