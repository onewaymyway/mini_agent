import { useQuery } from "@tanstack/react-query";
import { getFairnessDiagnostics, getGatingHistory, listCronJobs, listGoals } from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useGlobalSchedule() {
  const token = useAuthStore((s) => s.token);
  const cronJobs = useQuery({ queryKey: ["cron-jobs"], queryFn: listCronJobs, enabled: !!token, refetchInterval: 15000 });
  const goals = useQuery({ queryKey: ["goals"], queryFn: listGoals, enabled: !!token, refetchInterval: 15000 });
  const gating = useQuery({ queryKey: ["gating-history"], queryFn: () => getGatingHistory(50), enabled: !!token, refetchInterval: 15000 });
  const fairness = useQuery({ queryKey: ["fairness-diagnostics"], queryFn: getFairnessDiagnostics, enabled: !!token, refetchInterval: 15000 });
  return { cronJobs, goals, gating, fairness };
}
