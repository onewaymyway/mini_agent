import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getErrorLogStats,
  getFairnessDiagnostics,
  getGoalStuckStats,
  getLlmCallStats,
  getLlmPoolStatus,
  getSelfConfig,
  getSelfStatus,
  patchSelfConfig,
} from "../api/endpoints";

export function useSelfStatus() {
  return useQuery({ queryKey: ["self-status"], queryFn: getSelfStatus, refetchInterval: 8000 });
}
export function useLlmPoolStatus() {
  return useQuery({ queryKey: ["llm-pool-status"], queryFn: getLlmPoolStatus, refetchInterval: 8000 });
}
export function useFairnessDiagnostics() {
  return useQuery({ queryKey: ["fairness-diagnostics"], queryFn: getFairnessDiagnostics });
}
export function useLlmCallStats(days = 7) {
  return useQuery({ queryKey: ["llm-call-stats", days], queryFn: () => getLlmCallStats(days) });
}
export function useGoalStuckStats() {
  return useQuery({ queryKey: ["goal-stuck-stats"], queryFn: getGoalStuckStats });
}
export function useErrorLogStats() {
  return useQuery({ queryKey: ["error-log-stats"], queryFn: getErrorLogStats });
}
export function useSelfConfig() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["self-config"], queryFn: getSelfConfig });
  const save = useMutation({
    mutationFn: patchSelfConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["self-config"] }),
  });
  return { ...query, save };
}
