import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getAutonomousStatus,
  getInbox,
  getSentinelSummary,
  listPendingInteractions,
  listPendingPermissions,
  pauseScheduling,
  resumeScheduling,
  respondInteraction,
  respondPermission,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

/** 待审批权限请求 + 待回答交互请求，供 Topbar 徽标和 Chat 内联审批面板复用。*/
export function usePendingApprovals() {
  const token = useAuthStore((s) => s.token);
  const qc = useQueryClient();

  const permissions = useQuery({
    queryKey: ["permissions-pending"],
    queryFn: listPendingPermissions,
    enabled: !!token,
    refetchInterval: 4000,
  });
  const interactions = useQuery({
    queryKey: ["interactions-pending"],
    queryFn: listPendingInteractions,
    enabled: !!token,
    refetchInterval: 4000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["permissions-pending"] });
    qc.invalidateQueries({ queryKey: ["interactions-pending"] });
    qc.invalidateQueries({ queryKey: ["status"] });
  };

  const respondPerm = useMutation({
    mutationFn: ({ reqId, decision, remember }: { reqId: string; decision: string; remember?: boolean }) =>
      respondPermission(reqId, { decision, remember }),
    onSuccess: invalidate,
  });
  const respondIx = useMutation({
    mutationFn: ({ reqId, answer }: { reqId: string; answer: string }) => respondInteraction(reqId, { answer }),
    onSuccess: invalidate,
  });

  return {
    permissions: permissions.data?.pending || [],
    interactions: interactions.data?.pending || [],
    respondPerm,
    respondIx,
  };
}

/** Topbar：自治调度状态（排队/当前任务/暂停控制）+ 哨兵摘要 + 全局待办中心。*/
export function useTopbarModules() {
  const token = useAuthStore((s) => s.token);
  const qc = useQueryClient();

  const autonomous = useQuery({
    queryKey: ["autonomous-status"],
    queryFn: getAutonomousStatus,
    enabled: !!token,
    refetchInterval: 5000,
  });
  const sentinel = useQuery({
    queryKey: ["sentinel-summary"],
    queryFn: getSentinelSummary,
    enabled: !!token,
    refetchInterval: 15000,
  });
  const inbox = useQuery({
    queryKey: ["inbox"],
    queryFn: getInbox,
    enabled: !!token,
    refetchInterval: 15000,
  });

  const pause = useMutation({
    mutationFn: pauseScheduling,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["autonomous-status"] }),
  });
  const resume = useMutation({
    mutationFn: resumeScheduling,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["autonomous-status"] }),
  });

  return { autonomous, sentinel, inbox, pause, resume };
}
