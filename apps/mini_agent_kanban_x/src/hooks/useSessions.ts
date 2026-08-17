import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteSession,
  getSessionDetail,
  listSessions,
  newSession,
  resumeSession,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useSessions() {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
    enabled: !!token,
    refetchInterval: 8000,
  });
}

export function useSessionDetail(id: string | undefined) {
  return useQuery({
    queryKey: ["session", id],
    queryFn: () => getSessionDetail(id as string),
    enabled: !!id,
  });
}

export function useSessionActions() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["sessions"] });

  const create = useMutation({ mutationFn: newSession, onSuccess: invalidate });
  const resume = useMutation({ mutationFn: (id: string) => resumeSession(id), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id: string) => deleteSession(id), onSuccess: invalidate });

  return { create, resume, remove };
}
