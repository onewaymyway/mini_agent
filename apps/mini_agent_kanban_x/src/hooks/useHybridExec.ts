import { useQuery } from "@tanstack/react-query";
import { getHybridExecSummary } from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useHybridExec() {
  const token = useAuthStore((s) => s.token);
  const summary = useQuery({
    queryKey: ["hybrid-exec-summary"],
    queryFn: getHybridExecSummary,
    enabled: !!token,
    refetchInterval: 15000,
  });
  return { summary };
}
