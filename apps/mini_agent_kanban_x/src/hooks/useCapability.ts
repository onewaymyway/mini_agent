import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  acceptCapabilitySuggestion,
  answerCapabilityQuestion,
  createCapabilityTrack,
  deleteCapabilityTrack,
  dismissCapabilityQuestion,
  dismissCapabilitySuggestion,
  draftCapabilityPersona,
  getCapabilityPersonaDraft,
  getCapabilityTrack,
  getCapabilityTrackLedger,
  getCapabilityWikiPage,
  listCapabilityPersonas,
  listCapabilityQuestions,
  listCapabilitySuggestions,
  listCapabilityTracks,
  publishCapabilityPersona,
  setCapabilityPersonaWikiScopes,
  updateCapabilityTrack,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useCapabilityTracks(status?: string) {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["capability-tracks", status],
    queryFn: () => listCapabilityTracks(status),
    enabled: !!token,
  });
}

export function useCapabilityTrack(trackId: string | undefined) {
  return useQuery({
    queryKey: ["capability-track", trackId],
    queryFn: () => getCapabilityTrack(trackId as string),
    enabled: !!trackId,
  });
}

export function useCapabilityTrackLedger(trackId: string | undefined) {
  return useQuery({
    queryKey: ["capability-track-ledger", trackId],
    queryFn: () => getCapabilityTrackLedger(trackId as string),
    enabled: !!trackId,
  });
}

export function useCapabilityQuestions() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["capability-questions"], queryFn: () => listCapabilityQuestions(), enabled: !!token, refetchInterval: 20000 });
}

export function useCapabilitySuggestions() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["capability-suggestions"], queryFn: () => listCapabilitySuggestions(), enabled: !!token, refetchInterval: 20000 });
}

export function useCapabilityPersonas() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["capability-personas"], queryFn: listCapabilityPersonas, enabled: !!token });
}

export function useCapabilityPersonaDraft(trackId: string | undefined) {
  return useQuery({
    queryKey: ["capability-persona-draft", trackId],
    queryFn: () => getCapabilityPersonaDraft(trackId as string),
    enabled: !!trackId,
    retry: false,
  });
}

export function useCapabilityWikiPage(pageId: string | undefined) {
  return useQuery({
    queryKey: ["capability-wiki-page", pageId],
    queryFn: () => getCapabilityWikiPage(pageId as string),
    enabled: !!pageId,
  });
}

export function useCapabilityActions() {
  const qc = useQueryClient();
  const invalidateTracks = () => qc.invalidateQueries({ queryKey: ["capability-tracks"] });
  const invalidateTrack = (trackId?: string) => {
    invalidateTracks();
    if (trackId) {
      qc.invalidateQueries({ queryKey: ["capability-track", trackId] });
      qc.invalidateQueries({ queryKey: ["capability-track-ledger", trackId] });
    }
  };

  return {
    createTrack: useMutation({ mutationFn: createCapabilityTrack, onSuccess: invalidateTracks }),
    updateTrack: useMutation({
      mutationFn: ({ trackId, body }: { trackId: string; body: Parameters<typeof updateCapabilityTrack>[1] }) =>
        updateCapabilityTrack(trackId, body),
      onSuccess: (_r, { trackId }) => invalidateTrack(trackId),
    }),
    deleteTrack: useMutation({ mutationFn: (trackId: string) => deleteCapabilityTrack(trackId), onSuccess: invalidateTracks }),
    answerQuestion: useMutation({
      mutationFn: ({ questionId, answer }: { questionId: string; answer: string }) => answerCapabilityQuestion(questionId, answer),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["capability-questions"] }),
    }),
    dismissQuestion: useMutation({
      mutationFn: (questionId: string) => dismissCapabilityQuestion(questionId),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["capability-questions"] }),
    }),
    acceptSuggestion: useMutation({
      mutationFn: (suggestionId: string) => acceptCapabilitySuggestion(suggestionId),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["capability-suggestions"] });
        invalidateTracks();
      },
    }),
    dismissSuggestion: useMutation({
      mutationFn: (suggestionId: string) => dismissCapabilitySuggestion(suggestionId),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["capability-suggestions"] }),
    }),
    setPersonaWikiScopes: useMutation({
      mutationFn: ({ personaName, wikiScopes }: { personaName: string; wikiScopes: string[] }) =>
        setCapabilityPersonaWikiScopes(personaName, wikiScopes),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["capability-personas"] }),
    }),
    draftPersona: useMutation({
      mutationFn: (trackId: string) => draftCapabilityPersona(trackId),
      onSuccess: (_r, trackId) => qc.invalidateQueries({ queryKey: ["capability-persona-draft", trackId] }),
    }),
    publishPersona: useMutation({
      mutationFn: (trackId: string) => publishCapabilityPersona(trackId),
      onSuccess: (_r, trackId) => invalidateTrack(trackId),
    }),
  };
}
