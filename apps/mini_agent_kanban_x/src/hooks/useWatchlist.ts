import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ackPendingReport,
  getNotificationDispatchLog,
  getNotificationReportTiers,
  getNotificationWatchlist,
  getPendingReports,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useWatchlist() {
  const token = useAuthStore((s) => s.token);
  const queryClient = useQueryClient();

  const watchlist = useQuery({
    queryKey: ["notification-watchlist"],
    queryFn: getNotificationWatchlist,
    enabled: !!token,
  });
  const reportTiers = useQuery({
    queryKey: ["notification-report-tiers"],
    queryFn: getNotificationReportTiers,
    enabled: !!token,
    refetchInterval: 20000,
  });
  const pendingReports = useQuery({
    queryKey: ["notifications-pending"],
    queryFn: () => getPendingReports(20, 0),
    enabled: !!token,
    refetchInterval: 15000,
  });
  const dispatchLog = useQuery({
    queryKey: ["notification-dispatch-log"],
    queryFn: () => getNotificationDispatchLog(50),
    enabled: !!token,
    refetchInterval: 20000,
  });

  const ackReport = useMutation({
    mutationFn: (id: string) => ackPendingReport(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications-pending"] }),
  });

  return { watchlist, reportTiers, pendingReports, dispatchLog, ackReport };
}
