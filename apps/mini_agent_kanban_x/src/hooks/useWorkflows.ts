import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveWorkflowStep,
  cancelWorkflowRun,
  getWorkflowRunDetail,
  getWorkflowStats,
  getWorkflowYaml,
  listWorkflowRuns,
  listWorkflows,
  markWorkflowRunInterrupted,
  overrideWorkflowStepOutput,
  pauseWorkflowRun,
  previewWorkflow,
  provideWorkflowInput,
  rejectWorkflowStep,
  resumeWorkflowRun,
  runWorkflow,
} from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useWorkflows() {
  const token = useAuthStore((s) => s.token);
  return useQuery({ queryKey: ["workflows"], queryFn: listWorkflows, enabled: !!token });
}

export function useWorkflowYaml(name: string | undefined) {
  return useQuery({
    queryKey: ["workflow-yaml", name],
    queryFn: () => getWorkflowYaml(name as string),
    enabled: !!name,
  });
}

export function useWorkflowStats(name: string | undefined) {
  return useQuery({
    queryKey: ["workflow-stats", name],
    queryFn: () => getWorkflowStats(name as string),
    enabled: !!name,
  });
}

export function useWorkflowRuns(name?: string) {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["workflow-runs", name],
    queryFn: () => listWorkflowRuns(name),
    enabled: !!token,
    refetchInterval: 6000,
  });
}

export function useWorkflowRunDetail(runId: string | undefined) {
  return useQuery({
    queryKey: ["workflow-run", runId],
    queryFn: () => getWorkflowRunDetail(runId as string),
    enabled: !!runId,
    refetchInterval: 4000,
  });
}

export function useWorkflowActions() {
  const qc = useQueryClient();
  const invalidateRuns = () => qc.invalidateQueries({ queryKey: ["workflow-runs"] });
  const invalidateRun = (runId?: string) => {
    invalidateRuns();
    if (runId) qc.invalidateQueries({ queryKey: ["workflow-run", runId] });
  };

  return {
    preview: useMutation({ mutationFn: ({ name, inputs }: { name: string; inputs: unknown }) => previewWorkflow(name, inputs) }),
    run: useMutation({
      mutationFn: ({ name, body }: { name: string; body: Parameters<typeof runWorkflow>[1] }) => runWorkflow(name, body),
      onSuccess: invalidateRuns,
    }),
    pause: useMutation({ mutationFn: (runId: string) => pauseWorkflowRun(runId), onSuccess: (_r, runId) => invalidateRun(runId) }),
    cancel: useMutation({ mutationFn: (runId: string) => cancelWorkflowRun(runId), onSuccess: (_r, runId) => invalidateRun(runId) }),
    markInterrupted: useMutation({
      mutationFn: (runId: string) => markWorkflowRunInterrupted(runId),
      onSuccess: (_r, runId) => invalidateRun(runId),
    }),
    resume: useMutation({
      mutationFn: ({ runId, body }: { runId: string; body?: { background?: boolean; force_rerun_from?: string } }) =>
        resumeWorkflowRun(runId, body),
      onSuccess: (_r, { runId }) => invalidateRun(runId),
    }),
    approve: useMutation({ mutationFn: (runId: string) => approveWorkflowStep(runId), onSuccess: (_r, runId) => invalidateRun(runId) }),
    reject: useMutation({
      mutationFn: ({ runId, reason }: { runId: string; reason: string }) => rejectWorkflowStep(runId, reason),
      onSuccess: (_r, { runId }) => invalidateRun(runId),
    }),
    provideInput: useMutation({
      mutationFn: ({ runId, text }: { runId: string; text: string }) => provideWorkflowInput(runId, text),
      onSuccess: (_r, { runId }) => invalidateRun(runId),
    }),
    overrideStep: useMutation({
      mutationFn: ({ runId, stepId, output }: { runId: string; stepId: string; output: string }) =>
        overrideWorkflowStepOutput(runId, stepId, output),
      onSuccess: (_r, { runId }) => invalidateRun(runId),
    }),
  };
}
