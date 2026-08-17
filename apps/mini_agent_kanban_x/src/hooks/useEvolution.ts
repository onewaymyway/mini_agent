import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getEvolutionFeedbackLoopSummary, getEvolutionProposalDiff, listEvolutionProposals, mergeEvolutionProposal } from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useEvolutionProposals() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["evolution-proposals"], queryFn: listEvolutionProposals, enabled: !!token, refetchInterval: 30000 });
}

export function useEvolutionProposalDiff(branch: string | undefined) {
  return useQuery({
    queryKey: ["evolution-proposal-diff", branch],
    queryFn: () => getEvolutionProposalDiff(branch as string),
    enabled: !!branch,
  });
}

export function useEvolutionFeedbackLoopSummary(enabled: boolean) {
  return useQuery({ queryKey: ["evolution-feedback-loop"], queryFn: getEvolutionFeedbackLoopSummary, enabled });
}

export function useEvolutionActions() {
  const qc = useQueryClient();
  return {
    merge: useMutation({
      mutationFn: ({ branch, force }: { branch: string; force?: boolean }) => mergeEvolutionProposal(branch, force),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["evolution-proposals"] }),
    }),
  };
}
