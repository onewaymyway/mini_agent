import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ackGrowthFirstTouch,
  addGrowthKeyword,
  adoptGrowthCandidateGoal,
  confirmGrowthKeyword,
  generateGrowthMaterial,
  getGrowthAlign,
  getGrowthCandidateTimeline,
  getGrowthFollowups,
  getGrowthMaterialBody,
  getGrowthPursuits,
  getGrowthPursuitsPortfolioSummary,
  getGrowthReportBody,
  getGrowthSummary,
  growthAlignAdoptAll,
  growthAlignConfirmMatch,
  growthCandidateAction,
  recordGrowthFollowup,
  refreshGrowthCandidateReport,
  removeGrowthKeyword,
  restoreGrowthKeyword,
  runGrowthScan,
  viewGrowthPursuitMaterial,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useGrowthSummary() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["growth-summary"], queryFn: getGrowthSummary, enabled: !!token, refetchInterval: 20000 });
}

export function useGrowthFollowups() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["growth-followups"], queryFn: getGrowthFollowups, enabled: !!token });
}

export function useGrowthPursuits() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["growth-pursuits"], queryFn: getGrowthPursuits, enabled: !!token });
}

export function useGrowthPortfolioSummary(enabled: boolean) {
  return useQuery({
    queryKey: ["growth-portfolio-summary"],
    queryFn: getGrowthPursuitsPortfolioSummary,
    enabled,
  });
}

export function useGrowthAlign(enabled: boolean) {
  return useQuery({ queryKey: ["growth-align"], queryFn: getGrowthAlign, enabled });
}

export function useGrowthCandidateTimeline(candidateId: string | undefined) {
  return useQuery({
    queryKey: ["growth-candidate-timeline", candidateId],
    queryFn: () => getGrowthCandidateTimeline(candidateId as string),
    enabled: !!candidateId,
  });
}

export function useGrowthReportBody(reportId: string | undefined) {
  return useQuery({
    queryKey: ["growth-report-body", reportId],
    queryFn: () => getGrowthReportBody(reportId as string),
    enabled: !!reportId,
  });
}

export function useGrowthMaterialBody(materialId: string | undefined) {
  return useQuery({
    queryKey: ["growth-material-body", materialId],
    queryFn: () => getGrowthMaterialBody(materialId as string),
    enabled: !!materialId,
  });
}

export function useGrowthActions() {
  const qc = useQueryClient();
  const invalidateSummary = () => {
    qc.invalidateQueries({ queryKey: ["growth-summary"] });
    qc.invalidateQueries({ queryKey: ["growth-followups"] });
    qc.invalidateQueries({ queryKey: ["growth-pursuits"] });
    qc.invalidateQueries({ queryKey: ["growth-align"] });
  };

  return {
    ackFirstTouch: useMutation({ mutationFn: ackGrowthFirstTouch, onSuccess: invalidateSummary }),
    scan: useMutation({ mutationFn: runGrowthScan, onSuccess: invalidateSummary }),
    candidateAction: useMutation({
      mutationFn: ({ candidateId, action, reason }: { candidateId: string; action: "accept" | "dismiss"; reason?: string }) =>
        growthCandidateAction(candidateId, action, reason),
      onSuccess: invalidateSummary,
    }),
    followup: useMutation({
      mutationFn: ({ candidateId, outcome }: { candidateId: string; outcome: "progressed" | "stalled" }) =>
        recordGrowthFollowup(candidateId, outcome),
      onSuccess: invalidateSummary,
    }),
    addKeyword: useMutation({
      mutationFn: ({ topic, keywords }: { topic: string; keywords: string }) => addGrowthKeyword(topic, keywords),
      onSuccess: invalidateSummary,
    }),
    confirmKeyword: useMutation({ mutationFn: (topic: string) => confirmGrowthKeyword(topic), onSuccess: invalidateSummary }),
    removeKeyword: useMutation({ mutationFn: (topic: string) => removeGrowthKeyword(topic), onSuccess: invalidateSummary }),
    restoreKeyword: useMutation({ mutationFn: (topic: string) => restoreGrowthKeyword(topic), onSuccess: invalidateSummary }),
    adoptGoal: useMutation({ mutationFn: (candidateId: string) => adoptGrowthCandidateGoal(candidateId), onSuccess: invalidateSummary }),
    refreshReport: useMutation({ mutationFn: (candidateId: string) => refreshGrowthCandidateReport(candidateId), onSuccess: invalidateSummary }),
    generateMaterial: useMutation({ mutationFn: (candidateId: string) => generateGrowthMaterial(candidateId), onSuccess: invalidateSummary }),
    viewPursuitMaterial: useMutation({ mutationFn: (goalId: string) => viewGrowthPursuitMaterial(goalId), onSuccess: invalidateSummary }),
    alignAdoptAll: useMutation({ mutationFn: growthAlignAdoptAll, onSuccess: invalidateSummary }),
    alignConfirmMatch: useMutation({
      mutationFn: ({ topic, goalId }: { topic: string; goalId: string }) => growthAlignConfirmMatch(topic, goalId),
      onSuccess: invalidateSummary,
    }),
  };
}
