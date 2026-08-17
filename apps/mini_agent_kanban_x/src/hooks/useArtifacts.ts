import { useQuery } from "@tanstack/react-query";
import { getArtifact, listArtifacts } from "../api/endpoints";

export function useArtifactsList() {
  return useQuery({ queryKey: ["artifacts"], queryFn: listArtifacts });
}

export function useArtifactDetail(manifestId: string | undefined) {
  return useQuery({
    queryKey: ["artifact", manifestId],
    queryFn: () => getArtifact(manifestId as string),
    enabled: !!manifestId,
  });
}
