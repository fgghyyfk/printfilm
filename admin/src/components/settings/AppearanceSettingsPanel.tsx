import { useEffect, useState } from "react";
import { Check, ExternalLink, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { SITE_THEME_META, siteOrigin } from "@/lib/siteThemes";
import { cn } from "@/lib/utils";

/** 外观主题：选中后立即对全站生效，可先在新窗口预览。 */
export function AppearanceSettingsPanel() {
  /* current 当前全站主题；saving 正在切换的主题；error/notice 操作反馈 */
  const [current, setCurrent] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let alive = true;
    api<{ theme: string }>("/api/admin/settings/appearance")
      .then((res) => {
        if (alive) setCurrent(res.theme);
      })
      .catch((err: Error) => {
        if (alive) setError(err.message || "读取主题失败");
      });
    return () => {
      alive = false;
    };
  }, []);

  // 切换全站主题，失败时保持原主题并提示
  async function apply(id: string, name: string) {
    if (saving || id === current) return;
    if (!window.confirm(`把全站主题切换为「${name}」？所有用户刷新页面后生效。`)) return;
    setSaving(id);
    setError("");
    setNotice("");
    try {
      const res = await api<{ theme: string }>("/api/admin/settings/appearance", {
        method: "PUT",
        body: JSON.stringify({ theme: id }),
      });
      setCurrent(res.theme);
      setNotice(`已切换为「${name}」，用户刷新页面后生效。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "切换失败，请重试");
    } finally {
      setSaving(null);
    }
  }

  return (
    <SettingsPanel title="外观主题" description="决定用户端全站的配色、字体和按钮样式，不影响管理后台。">
      {current === null && !error ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在读取当前主题…
        </p>
      ) : null}
      {error ? <p className="mb-3 text-sm text-red-600" role="alert">{error}</p> : null}
      {notice ? <p className="mb-3 text-sm text-emerald-700" role="status">{notice}</p> : null}
      {current !== null ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {SITE_THEME_META.map((theme) => {
            const active = theme.id === current;
            const [bg, accent, ink] = theme.swatches;
            return (
              <article
                key={theme.id}
                className={cn("overflow-hidden rounded-xl border bg-card", active ? "border-primary ring-2 ring-primary/20" : "border-border")}
              >
                <div className="flex h-32 flex-col justify-center gap-2 px-5" style={{ background: bg, color: ink }} aria-hidden>
                  <strong style={{ fontFamily: theme.serif ? "'Noto Serif SC', 'Songti SC', serif" : "inherit", fontSize: 22 }}>
                    一句话，图片视频一站做
                  </strong>
                  <span className="inline-flex w-fit rounded-md px-3 py-1 text-xs font-semibold text-white" style={{ background: accent }}>
                    立即制作
                  </span>
                </div>
                <div className="space-y-3 p-4">
                  <div className="flex items-center gap-2">
                    <h4 className="font-semibold">{theme.name}</h4>
                    {active ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-primary">
                        <Check className="h-3.5 w-3.5" />
                        当前使用
                      </span>
                    ) : null}
                  </div>
                  <p className="text-sm text-muted-foreground">{theme.description}</p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="admin-btn admin-btn-primary"
                      disabled={active || saving !== null}
                      onClick={() => void apply(theme.id, theme.name)}
                    >
                      {saving === theme.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                      {active ? "使用中" : "应用到全站"}
                    </button>
                    <a
                      className="admin-btn"
                      href={`${siteOrigin()}/?preview_theme=${theme.id}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      预览
                    </a>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </SettingsPanel>
  );
}
