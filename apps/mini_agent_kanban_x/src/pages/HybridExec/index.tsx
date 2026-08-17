import { Alert, Card, Collapse, Descriptions, Empty, Tag, Typography } from "antd";
import { useHybridExec } from "../../hooks/useHybridExec";

const { Title } = Typography;

const statusColor: Record<string, string> = {
  active: "green",
  retired: "default",
  none: "default",
  failing: "red",
};

export default function HybridExec() {
  const { summary } = useHybridExec();
  const tasks = summary.data?.tasks || [];

  return (
    <div>
      <Title level={4}>🧪 混合执行（脚本 / LLM / Agent）</Title>
      {summary.data?._error && <Alert type="error" message={summary.data._error} style={{ marginBottom: 16 }} />}

      <Card loading={summary.isLoading}>
        {tasks.length === 0 ? (
          <Empty description="暂无 hybrid_exec 任务" />
        ) : (
          <Collapse
            items={tasks.map((t) => ({
              key: t.task_id,
              label: (
                <>
                  {t.task_id}{" "}
                  <Tag color={statusColor[t.active_status || "none"] || "default"}>{t.active_status || "none"}</Tag>
                  {t.active_consecutive_fail ? <Tag color="volcano">连续失败 {t.active_consecutive_fail}</Tag> : null}
                </>
              ),
              children: (
                <>
                  <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
                    <Descriptions.Item label="当前版本">{t.active_version ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="版本总数">{t.version_count ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="创建者">{t.active_created_by ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="成功次数">{t.active_success_count ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="失败次数">{t.active_fail_count ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="连续失败">{t.active_consecutive_fail ?? "—"}</Descriptions.Item>
                  </Descriptions>
                  <pre style={{ whiteSpace: "pre-wrap", background: "#fafafa", padding: 8 }}>
                    {JSON.stringify(t.run_summary ?? {}, null, 2)}
                  </pre>
                </>
              ),
            }))}
          />
        )}
      </Card>
    </div>
  );
}
