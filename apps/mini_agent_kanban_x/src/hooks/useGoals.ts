import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyTuningProposal,
  cancelObjective,
  closeCheckExecutionSpec,
  confirmExecutionSpec,
  confirmTuningProposal,
  createGoal,
  editObjectiveStep,
  feedbackGoal,
  generateExecutionSpec,
  getCycleDiagnostics,
  getExecutionSpec,
  lightweightNextCycle,
  listGoals,
  listTuningProposals,
  migrateLegacyCycles,
  pauseObjective,
  recurGoal,
  rejectTuningProposal,
  resetObjectiveStep,
  resumeObjective,
  retryObjective,
  reviseExecutionSpec,
  skipNextCycle,
  suggestTuningProposal,
  unrecurGoal,
  updateGoal,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useGoals() {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["goals"],
    queryFn: listGoals,
    enabled: !!token,
    refetchInterval: 10000,
  });
}

export function useGoalActions() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["goals"] });

  return {
    create: useMutation({ mutationFn: createGoal, onSuccess: invalidate }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Parameters<typeof updateGoal>[1] }) => updateGoal(id, body),
      onSuccess: invalidate,
    }),
    recur: useMutation({ mutationFn: (id: string) => recurGoal(id), onSuccess: invalidate }),
    unrecur: useMutation({ mutationFn: (id: string) => unrecurGoal(id), onSuccess: invalidate }),
    skipNext: useMutation({ mutationFn: (id: string) => skipNextCycle(id), onSuccess: invalidate }),
    lightweightNext: useMutation({ mutationFn: (id: string) => lightweightNextCycle(id), onSuccess: invalidate }),
    migrateLegacy: useMutation({ mutationFn: (id: string) => migrateLegacyCycles(id), onSuccess: invalidate }),
    feedback: useMutation({
      mutationFn: ({ id, text }: { id: string; text: string }) => feedbackGoal(id, text),
      onSuccess: invalidate,
    }),
  };
}

export function useExecutionSpec(goalId: string | undefined) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["execution-spec", goalId] });
    qc.invalidateQueries({ queryKey: ["goals"] });
  };
  const query = useQuery({
    queryKey: ["execution-spec", goalId],
    queryFn: () => getExecutionSpec(goalId as string),
    enabled: !!goalId,
  });

  return {
    ...query,
    // 四个写操作全部用 onSettled（成功/失败都触发）替代 onSuccess 单独触发
    // invalidate：失败时也强制重新拉取 `/execution_spec`，让界面回到后端
    // 真实持久化的状态，而不是停留在"修订前"的旧缓存上、让用户误以为
    // 修订已经生效或者界面卡住了。全局 onError（见 main.tsx）负责弹出
    // 报错提示，这里只负责状态同步。
    generate: useMutation({
      mutationFn: (body?: Parameters<typeof generateExecutionSpec>[1]) =>
        generateExecutionSpec(goalId as string, body),
      onSettled: invalidate,
    }),
    revise: useMutation({
      mutationFn: ({ feedback, locked }: { feedback: string; locked?: string[] }) =>
        reviseExecutionSpec(goalId as string, feedback, locked),
      onSettled: invalidate,
    }),
    confirm: useMutation({ mutationFn: () => confirmExecutionSpec(goalId as string), onSettled: invalidate }),
    closeCheck: useMutation({ mutationFn: () => closeCheckExecutionSpec(goalId as string), onSettled: invalidate }),
  };
}

export function useCycleDiagnostics(goalId: string | undefined) {
  return useQuery({
    queryKey: ["cycle-diagnostics", goalId],
    queryFn: () => getCycleDiagnostics(goalId as string),
    enabled: !!goalId,
  });
}

export function useTuningProposals(goalId: string | undefined) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["tuning-proposals", goalId] });
  const query = useQuery({
    queryKey: ["tuning-proposals", goalId],
    queryFn: () => listTuningProposals(goalId as string),
    enabled: !!goalId,
  });
  return {
    ...query,
    suggest: useMutation({ mutationFn: () => suggestTuningProposal(goalId as string), onSuccess: invalidate }),
    confirm: useMutation({
      mutationFn: (proposalId: string) => confirmTuningProposal(goalId as string, proposalId),
      onSuccess: invalidate,
    }),
    apply: useMutation({
      mutationFn: (proposalId: string) => applyTuningProposal(goalId as string, proposalId),
      onSuccess: invalidate,
    }),
    reject: useMutation({
      mutationFn: (proposalId: string) => rejectTuningProposal(goalId as string, proposalId),
      onSuccess: invalidate,
    }),
  };
}

export function useObjectiveActions() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["goals"] });
  return {
    cancel: useMutation({ mutationFn: (id: string) => cancelObjective(id), onSuccess: invalidate }),
    pause: useMutation({ mutationFn: (id: string) => pauseObjective(id), onSuccess: invalidate }),
    resume: useMutation({ mutationFn: (id: string) => resumeObjective(id), onSuccess: invalidate }),
    retry: useMutation({ mutationFn: (id: string) => retryObjective(id), onSuccess: invalidate }),
    editStep: useMutation({
      mutationFn: ({ id, idx, body }: { id: string; idx: number; body: unknown }) => editObjectiveStep(id, idx, body),
      onSuccess: invalidate,
    }),
    resetStep: useMutation({
      mutationFn: ({ id, idx }: { id: string; idx: number }) => resetObjectiveStep(id, idx),
      onSuccess: invalidate,
    }),
  };
}
