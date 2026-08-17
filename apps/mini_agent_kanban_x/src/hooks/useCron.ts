import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addCronJobFeedback,
  createCronJob,
  deleteCronJob,
  getCronJobPrompt,
  getCronJobRunEvents,
  getCronJobWorkspace,
  listCronJobs,
  resetCronJobWorkspace,
  runCronJobNow,
  updateCronJob,
  updateCronJobPrompt,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useCronJobs() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["cron-jobs"], queryFn: listCronJobs, enabled: !!token, refetchInterval: 10000 });
}

export function useCronJobWorkspace(jobId: string | undefined) {
  return useQuery({
    queryKey: ["cron-job-workspace", jobId],
    queryFn: () => getCronJobWorkspace(jobId as string),
    enabled: !!jobId,
    refetchInterval: 8000,
  });
}

export function useCronJobPrompt(jobId: string | undefined) {
  return useQuery({
    queryKey: ["cron-job-prompt", jobId],
    queryFn: () => getCronJobPrompt(jobId as string),
    enabled: !!jobId,
  });
}

export function useCronJobRunEvents(jobId: string | undefined, runId: string | undefined) {
  return useQuery({
    queryKey: ["cron-job-run-events", jobId, runId],
    queryFn: () => getCronJobRunEvents(jobId as string, runId as string),
    enabled: !!jobId && !!runId,
  });
}

export function useCronActions() {
  const qc = useQueryClient();
  const invalidateJobs = () => qc.invalidateQueries({ queryKey: ["cron-jobs"] });
  const invalidateWorkspace = (jobId?: string) => {
    if (jobId) qc.invalidateQueries({ queryKey: ["cron-job-workspace", jobId] });
  };

  return {
    create: useMutation({ mutationFn: createCronJob, onSuccess: invalidateJobs }),
    update: useMutation({
      mutationFn: ({ jobId, body }: { jobId: string; body: Parameters<typeof updateCronJob>[1] }) => updateCronJob(jobId, body),
      onSuccess: invalidateJobs,
    }),
    remove: useMutation({ mutationFn: (jobId: string) => deleteCronJob(jobId), onSuccess: invalidateJobs }),
    runNow: useMutation({
      mutationFn: (jobId: string) => runCronJobNow(jobId),
      onSuccess: (_r, jobId) => {
        invalidateJobs();
        invalidateWorkspace(jobId);
      },
    }),
    feedback: useMutation({
      mutationFn: ({ jobId, text }: { jobId: string; text: string }) => addCronJobFeedback(jobId, text),
      onSuccess: invalidateJobs,
    }),
    updatePrompt: useMutation({
      mutationFn: ({ jobId, prompt }: { jobId: string; prompt: string }) => updateCronJobPrompt(jobId, prompt),
      onSuccess: (_r, { jobId }) => qc.invalidateQueries({ queryKey: ["cron-job-prompt", jobId] }),
    }),
    reset: useMutation({
      mutationFn: (jobId: string) => resetCronJobWorkspace(jobId),
      onSuccess: (_r, jobId) => {
        invalidateJobs();
        invalidateWorkspace(jobId);
      },
    }),
  };
}
