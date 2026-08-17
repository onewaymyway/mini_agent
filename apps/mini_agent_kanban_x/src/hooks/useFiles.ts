import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fsDelete, fsList, fsMkdir, fsRead, fsRename, fsWrite } from "../api/endpoints";

export function useFsList(path: string) {
  return useQuery({
    queryKey: ["fs-list", path],
    queryFn: () => fsList(path),
  });
}

export function useFsRead(path: string | undefined) {
  return useQuery({
    queryKey: ["fs-read", path],
    queryFn: () => fsRead(path as string),
    enabled: !!path,
  });
}

export function useFsActions(currentDir: string) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["fs-list", currentDir] });

  const write = useMutation({
    mutationFn: ({ path, content }: { path: string; content: string }) => fsWrite(path, content),
  });
  const mkdir = useMutation({ mutationFn: (path: string) => fsMkdir(path), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (path: string) => fsDelete(path), onSuccess: invalidate });
  const rename = useMutation({
    mutationFn: ({ path, newPath }: { path: string; newPath: string }) => fsRename(path, newPath),
    onSuccess: invalidate,
  });

  return { write, mkdir, remove, rename };
}
