import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, Search } from "lucide-react";
import { checkAdWords, fetchAdWordsOverview } from "@/api/compliance";
import type { AdWordCategory, AdWordsCheckResult, AdWordsOverview } from "@/api/types/settings";
import {
  LabeledControl,
  SettingsLoading,
  SettingsPanel,
  SettingsStatusBar,
  SettingsTabShell,
} from "@/components/settings/SettingsPanel";
import { useAdminModelSettings } from "@/hooks/useAdminModelSettings";
import { cn } from "@/lib/utils";

/** 套图规划单价上限（分），与后端校验一致 */
const PLAN_FEN_MAX = 100000;

// 解析自定义禁用词：按逗号/顿号/空白切分并去重
function parseExtraWords(raw: string): string[] {
  const words = raw
    .split(/[,，、;；\s]+/)
    .map((w) => w.trim())
    .filter(Boolean);
  return Array.from(new Set(words));
}

// 读取错误对象中的可读文案
function errText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

// 词库分类列表：分类名 + 词数 + 示例词 chips
function CategoryList({ title, level, items }: { title: string; level: "block" | "warn"; items: AdWordCategory[] }) {
  const total = items.reduce((sum, it) => sum + it.count, 0);
  return (
    <div className="min-w-0">
      <div className="settings-subsection-title flex items-center gap-2">
        <span className={cn("settings-cap-tag", level === "block" ? "is-warn" : "is-audio")}>{title}</span>
        <span className="text-xs font-normal text-muted-foreground">
          {items.length} 类 · 共 {total} 词
        </span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground">暂无分类</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((it) => (
            <li key={it.category} className="rounded-md border border-border/60 px-3 py-2">
              <div className="flex items-center justify-between gap-2 text-xs">
                <strong className="font-medium">{it.category}</strong>
                <span className="text-muted-foreground">{it.count} 词</span>
              </div>
              {it.examples.length > 0 ? (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {it.examples.map((w) => (
                    <span key={w} className="settings-cap-tag is-text">
                      {w}
                    </span>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// 试检区块：输入文本 → 调用 check 接口 → 展示清洗结果与命中词
function AdWordsChecker() {
  const [text, setText] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AdWordsCheckResult | null>(null);

  // 提交试检
  async function handleCheck() {
    if (!text.trim()) return;
    setChecking(true);
    setError(null);
    try {
      setResult(await checkAdWords(text));
    } catch (err) {
      setResult(null);
      setError(errText(err, "检测失败"));
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <LabeledControl label="待检测文案" hint="按已保存的词库检测；修改自定义词后需先保存才会生效">
        <textarea
          className="settings-input"
          rows={4}
          placeholder="例：全网最低价，国家级品质，买到就是赚到"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </LabeledControl>
      <div>
        <button
          type="button"
          className="admin-btn admin-btn-primary settings-mini-btn"
          disabled={checking || !text.trim()}
          onClick={() => void handleCheck()}
        >
          {checking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
          检测
        </button>
      </div>

      {error ? <p className="text-xs text-destructive">{error}</p> : null}

      {result ? (
        <div className="flex flex-col gap-3 rounded-md border border-border/60 p-3 text-xs">
          <div>
            <div className="mb-1 font-medium">命中词（{result.hits.length}）</div>
            {result.hits.length === 0 ? (
              <span className="text-muted-foreground">未命中任何词</span>
            ) : (
              <div className="flex flex-wrap gap-1">
                {result.hits.map((h, i) => (
                  <span
                    key={`${h.word}-${i}`}
                    className={cn("settings-cap-tag", h.level === "block" ? "is-warn" : "is-audio")}
                    title={h.category}
                  >
                    {h.word} · {h.level === "block" ? "拦截" : "提醒"} · {h.category}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="mb-1 font-medium">清洗后文本</div>
            <p className="whitespace-pre-wrap break-words rounded bg-muted/50 px-2 py-1.5">
              {result.clean || <span className="text-muted-foreground">（空）</span>}
            </p>
          </div>
          {result.messages.length > 0 ? (
            <div>
              <div className="mb-1 font-medium">提示</div>
              <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
                {result.messages.map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** 内容合规：广告法自定义禁用词、内置词库概览、试检与电商套图配置 */
export function ComplianceSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();

  /* 内置词库概览：数据 / 加载 / 错误 */
  const [overview, setOverview] = useState<AdWordsOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  // 拉取内置词库概览
  const loadOverview = useCallback(async () => {
    setOverviewLoading(true);
    setOverviewError(null);
    try {
      setOverview(await fetchAdWordsOverview());
    } catch (err) {
      setOverviewError(errText(err, "加载词库失败"));
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  const extraWords = useMemo(() => parseExtraWords(form?.ad_banned_extra ?? ""), [form?.ad_banned_extra]);

  // 只提交本页签的 3 个字段，保存后刷新概览
  async function handleSave() {
    if (!form) return;
    await save(
      {
        ad_banned_extra: form.ad_banned_extra,
        model_vision: form.model_vision,
        ecom_set_plan_fen: form.ecom_set_plan_fen,
      },
      "内容合规配置已保存",
    );
    void loadOverview();
  }

  if (loading || !form) {
    return <SettingsLoading />;
  }

  const planFen = Number.isFinite(form.ecom_set_plan_fen) ? form.ecom_set_plan_fen : 0;
  const blockTotal = overview?.block.reduce((s, c) => s + c.count, 0) ?? 0;
  const warnTotal = overview?.warn.reduce((s, c) => s + c.count, 0) ?? 0;

  return (
    <SettingsTabShell onSave={() => void handleSave()} saving={saving}>
      <SettingsStatusBar
        title="内容合规状态"
        items={[
          {
            id: "block",
            label: "拦截词",
            ready: blockTotal > 0,
            readyText: `${blockTotal} 词`,
            pendingText: overviewLoading ? "加载中" : "未加载",
          },
          {
            id: "warn",
            label: "提醒词",
            ready: warnTotal > 0,
            readyText: `${warnTotal} 词`,
            pendingText: overviewLoading ? "加载中" : "未加载",
          },
          {
            id: "extra",
            label: "自定义词",
            ready: extraWords.length > 0,
            readyText: `${extraWords.length} 词`,
            pendingText: "未配置",
          },
          {
            id: "vision",
            label: "视觉模型",
            ready: Boolean(form.model_vision?.trim()),
            readyText: form.model_vision || "已配置",
            pendingText: "未填写",
          },
        ]}
      />

      <div className="settings-routing-grid">
        <SettingsPanel
          className="settings-panel--compact"
          title="1. 自定义禁用词"
          description="全站 AI 生成文案统一过滤"
          actions={<span className="settings-status-extra">已解析 {extraWords.length} 词</span>}
        >
          <LabeledControl
            label="禁用词"
            hint="逗号、顿号或空白分隔"
            className="settings-field-span-full"
          >
            <textarea
              className="settings-input"
              rows={5}
              placeholder="例：秒杀全网、行业天花板、某竞品品牌名"
              value={form.ad_banned_extra ?? ""}
              onChange={(e) => patchField("ad_banned_extra", e.target.value)}
            />
          </LabeledControl>
          <p className="settings-panel-footnote">
            命中即删除；内置词库已覆盖广告法常见极限词，这里只需补充行业/品牌特有词。
          </p>
        </SettingsPanel>

        <SettingsPanel
          className="settings-panel--compact"
          title="2. 试检"
          description="输入一段文字，查看命中词与清洗结果"
        >
          <AdWordsChecker />
        </SettingsPanel>

        <SettingsPanel
          className="settings-panel--compact"
          title="3. 内置词库概览"
          description="拦截档自动替换/删除，提醒档仅提示；只读"
          actions={
            <button
              type="button"
              className="admin-btn settings-mini-btn"
              disabled={overviewLoading}
              onClick={() => void loadOverview()}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", overviewLoading && "animate-spin")} />
              刷新
            </button>
          }
        >
          {overviewLoading && !overview ? (
            <SettingsLoading label="加载词库…" />
          ) : overviewError ? (
            <p className="text-xs text-destructive">{overviewError}</p>
          ) : overview ? (
            <div className="flex flex-col gap-2">
              <CategoryList title="拦截" level="block" items={overview.block} />
              <CategoryList title="提醒" level="warn" items={overview.warn} />
            </div>
          ) : null}
        </SettingsPanel>

        <SettingsPanel
          className="settings-panel--compact"
          title="4. 电商套图"
          description="一图出套图：看商品图的视觉模型与规划计费"
        >
          <div className="settings-field-grid">
            <LabeledControl label="视觉理解模型" hint="默认 seed-2-0-lite-260428">
              <input
                className="settings-input"
                placeholder="seed-2-0-lite-260428"
                value={form.model_vision ?? ""}
                onChange={(e) => patchField("model_vision", e.target.value)}
              />
            </LabeledControl>
            <LabeledControl
              label="套图规划单价（分）"
              hint={`约 ¥${(planFen / 100).toFixed(2)} / 次；范围 0–${PLAN_FEN_MAX}`}
            >
              <input
                className="settings-input"
                type="number"
                min={0}
                max={PLAN_FEN_MAX}
                step={1}
                value={planFen}
                onChange={(e) => {
                  const n = Math.round(Number(e.target.value));
                  patchField("ecom_set_plan_fen", Number.isFinite(n) ? Math.min(Math.max(n, 0), PLAN_FEN_MAX) : 0);
                }}
              />
            </LabeledControl>
          </div>
        </SettingsPanel>
      </div>
    </SettingsTabShell>
  );
}
