import { Alert, Card, Skeleton, Tabs } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getDiagnostics } from "../../api/endpoints";
import {
  useErrorLogStats,
  useFairnessDiagnostics,
  useGoalStuckStats,
  useLlmCallStats,
  useLlmPoolStatus,
  useSelfConfig,
  useSelfStatus,
} from "../../hooks/useSelfStatus";

function JsonPanel({ isLoading, isError, error, data }: { isLoading: boolean; isError: boolean; error: unknown; data: unknown }) {
  if (isLoading) return <Skeleton active />;
  if (isError) return <Alert type="warning" showIcon message={(error as Error)?.message || "加载失败"} />;
  return <pre style={{ maxHeight: 480, overflow: "auto", background: "#fafafa", padding: 12 }}>{JSON.stringify(data, null, 2)}</pre>;
}

/**
 * 自我状态 / 诊断 / 错误日志：合并旧看板 Tab7（自我状态）、Tab16（诊断信息）、
 * Tab18（错误日志统计）为一个页面下的多个子 Tab，减少侧边菜单层级。
 */
export default function SelfStatus() {
  const selfStatus = useSelfStatus();
  const llmPool = useLlmPoolStatus();
  const fairness = useFairnessDiagnostics();
  const llmCalls = useLlmCallStats(7);
  const goalStuck = useGoalStuckStats();
  const errorLog = useErrorLogStats();
  const config = useSelfConfig();
  const diagnostics = useQuery({ queryKey: ["diagnostics-page"], queryFn: getDiagnostics });

  return (
    <Card>
      <Tabs
        items={[
          {
            key: "status",
            label: "运行状态",
            children: <JsonPanel {...selfStatus} />,
          },
          {
            key: "llm_pool",
            label: "LLM 调用池",
            children: <JsonPanel {...llmPool} />,
          },
          {
            key: "fairness",
            label: "公平性诊断",
            children: <JsonPanel {...fairness} />,
          },
          {
            key: "llm_calls",
            label: "调用统计（近7天）",
            children: <JsonPanel {...llmCalls} />,
          },
          {
            key: "goal_stuck",
            label: "目标卡住统计",
            children: <JsonPanel {...goalStuck} />,
          },
          {
            key: "config",
            label: "配置",
            children: <JsonPanel {...config} />,
          },
          {
            key: "diagnostics",
            label: "诊断信息",
            children: <JsonPanel {...diagnostics} />,
          },
          {
            key: "error_log",
            label: "错误日志统计",
            children: <JsonPanel {...errorLog} />,
          },
        ]}
      />
    </Card>
  );
}
