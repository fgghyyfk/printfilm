import { useCallback, useEffect, useState } from "react";
import { api, type AdminTaskEventPage } from "@/api/client";
import { taskEventPagePath } from "@/lib/taskEventPagination";
import { redactTaskText } from "@/lib/taskObservability";

/** Load only the selected history page; live refreshes never move an older page. */
export function useTaskEventPage(taskId: number, refreshToken: string, eventTypePrefix?: string) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<AdminTaskEventPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const liveRefresh = page === 1 ? refreshToken : "";

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void api<AdminTaskEventPage>(taskEventPagePath(taskId, page, pageSize, eventTypePrefix), { signal: controller.signal })
      .then((result) => { if (!cancelled) setData(result); })
      .catch((reason: unknown) => {
        if (!cancelled) setError(redactTaskText(reason instanceof Error ? reason.message : "事件加载失败"));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [taskId, page, pageSize, eventTypePrefix, liveRefresh, revision]);

  const changePageSize = useCallback((size: number) => { setPageSize(size); setPage(1); }, []);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return { data, page, pageSize, loading, error, setPage, changePageSize, refresh };
}
