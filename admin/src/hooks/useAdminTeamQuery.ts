import { useEffect, useState } from "react";
import { api } from "@/api/client";

/** 团队页面可取消查询；筛选/路由切换后旧响应不得覆盖新结果。 */
export function useAdminTeamQuery<T>(path: string) {
  /* result 绑定请求路径，version 支持显式重试或刷新。 */
  const [result, setResult] = useState<{ path: string; data: T | null; error: string; loading: boolean }>({ path, data: null, error: "", loading: true });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResult({ path, data: null, error: "", loading: true });
    void api<T>(path, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) setResult({ path, data, error: "", loading: false });
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setResult({ path, data: null, error: cause instanceof Error ? cause.message : "加载失败", loading: false });
    });
    return () => controller.abort();
  }, [path, version]);
  const visible = result.path === path ? result : { data: null, error: "", loading: true };
  return { ...visible, reload: () => setVersion((value) => value + 1) };
}
