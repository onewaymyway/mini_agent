import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  confirmNoveltyCandidate,
  dismissNoveltyCandidate,
  getFeedbackLoopSummary,
  listExternalInputAlerts,
  listExternalInputEvents,
  listExternalInputPolicies,
  listExternalInputSources,
  listNoveltyCandidates,
  reloadExternalInputSources,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useExternalInput() {
  const token = useAuthStore((s) => s.token);
  const queryClient = useQueryClient();

  const sources = useQuery({
    queryKey: ["external-input-sources"],
    queryFn: listExternalInputSources,
    enabled: !!token,
    refetchInterval: 15000,
  });
  const policies = useQuery({
    queryKey: ["external-input-policies"],
    queryFn: listExternalInputPolicies,
    enabled: !!token,
  });
  const alerts = useQuery({
    queryKey: ["external-input-alerts"],
    queryFn: () => listExternalInputAlerts(20, 0),
    enabled: !!token,
    refetchInterval: 15000,
  });
  const events = useQuery({
    queryKey: ["external-input-events"],
    queryFn: () => listExternalInputEvents(50, 0),
    enabled: !!token,
    refetchInterval: 15000,
  });
  const candidates = useQuery({
    queryKey: ["external-input-novelty-candidates"],
    queryFn: () => listNoveltyCandidates(20, 0),
    enabled: !!token,
    refetchInterval: 15000,
  });
  const feedbackLoop = useQuery({
    queryKey: ["evolution-feedback-loop-summary"],
    queryFn: getFeedbackLoopSummary,
    enabled: !!token,
  });

  const reload = useMutation({
    mutationFn: reloadExternalInputSources,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["external-input-sources"] });
      queryClient.invalidateQueries({ queryKey: ["external-input-events"] });
    },
  });
  const confirmCandidate = useMutation({
    mutationFn: (id: string) => confirmNoveltyCandidate(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["external-input-novelty-candidates"] }),
  });
  const dismissCandidate = useMutation({
    mutationFn: (id: string) => dismissNoveltyCandidate(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["external-input-novelty-candidates"] }),
  });

  return { sources, policies, alerts, events, candidates, feedbackLoop, reload, confirmCandidate, dismissCandidate };
}
