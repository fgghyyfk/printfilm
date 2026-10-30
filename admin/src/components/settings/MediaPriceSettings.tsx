import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { AdminField } from "@/components/admin/AdminField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsPanel } from "./SettingsPanel";

type PriceData = {
  active_version: number | null; enabled: boolean; prices: Record<string, Record<string, string>>;
  strategies: Array<{ id: string; capability: string; allowed_resolutions: string[]; duration_min?: number; duration_max?: number }>;
};
function priceError(value: string) {
  if (!value.trim()) return "";
  if (!/^\d+(?:\.\d{1,4})?$/.test(value.trim())) return "请输入金额，最多保留 4 位小数";
  return Number(value) < 0.0001 || Number(value) > 10000 ? "单价范围为 0.0001–10000 元" : "";
}

/** Price publishing is independent from the surrounding payment settings save. */
export function MediaPriceSettings() {
  const [data, setData] = useState<PriceData | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [operation, setOperation] = useState<"" | "load" | "publish" | "toggle">("");
  const baseline = useRef("");
  const busy = Boolean(operation);
  const dirty = Boolean(data && baseline.current !== JSON.stringify(data.prices));
  const invalidPrices = data ? Object.values(data.prices).some(rows => Object.values(rows).some(price => priceError(price))) : false;
  async function load() {
    try {
      const loaded = await api<PriceData>("/api/admin/settings/billing/media-prices");
      baseline.current = JSON.stringify(loaded.prices); setData(loaded); setError(""); return true;
    } catch (cause) { setError(cause instanceof Error ? cause.message : "价格加载失败"); return false; }
  }
  useEffect(() => { void load(); }, []);
  async function reload() {
    if (busy) return;
    setOperation("load"); setNotice("");
    try { await load(); } finally { setOperation(""); }
  }
  async function toggle() {
    if (!data?.active_version || busy) return;
    setOperation("toggle"); setNotice(""); setError("");
    try {
      await api("/api/admin/settings/billing/media-prices/activation", { method: "PATCH", body: JSON.stringify({ enabled: !data.enabled, expected_version: data.active_version }) });
      setData(current => current ? { ...current, enabled: !data.enabled } : current);
      setNotice("计费模式已切换，仅影响新任务，在途任务保持原规则。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "切换失败"); }
    finally { setOperation(""); }
  }
  async function publish() {
    if (!data || busy || invalidPrices) return;
    setOperation("publish"); setError(""); setNotice("");
    try {
      const prices = Object.fromEntries(Object.entries(data.prices).map(([model, rows]) => [model,
        Object.fromEntries(Object.entries(rows).filter(([, value]) => value.trim() !== "").map(([resolution, value]) => [resolution, value.trim()])),
      ]));
      await api("/api/admin/settings/billing/media-prices", { method: "POST", body: JSON.stringify({ prices, expected_version: data.active_version }) });
      const refreshed = await load();
      setNotice(refreshed ? "价格已发布，新任务立即生效，在途任务保持原价格。" : "价格已发布，最新版本读取失败，请重新加载后继续编辑。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "发布失败"); }
    finally { setOperation(""); }
  }
  return <SettingsPanel title="图片与视频价格" description="图片按张收费，视频按提交秒数收费。本区独立发布，修改支付配置不会发布价格。">
    <div className="admin-form-notice">{data?.active_version ? `当前价格版本 #${data.active_version} · ${data.enabled ? "固定计费已启用" : "固定计费已停用"}` : "尚未发布价格版本"}{dirty ? " · 有未发布修改" : ""}</div>
    <p className="settings-price-note">单价使用人民币，最多 4 位小数。留空表示该档位不可生成；视频总价 = 单价 × 提交秒数。</p>
    {error ? <p role="alert" className="admin-field-error">{error}</p> : null}
    {notice ? <p role="status" className="settings-price-note">{notice}</p> : null}
    {!data && !error ? <p className="settings-price-note" role="status">正在加载价格…</p> : null}
    {data && ["image", "video"].map(kind => <details key={kind} className="settings-advanced mt-4" open={kind === "image" ? true : undefined}>
      <summary>{kind === "image" ? "图片价格 · 元 / 张" : "视频价格 · 元 / 秒"}</summary>
      <div className="settings-price-table admin-table-wrap"><table><thead><tr><th>模型 / 支持范围</th><th>分辨率与单价</th></tr></thead><tbody>
        {data.strategies.filter(strategy => strategy.capability === kind).map(strategy => <tr key={strategy.id}>
          <td><strong className="text-xs">{strategy.id}</strong><p className="settings-price-note">{strategy.duration_max ? `${strategy.duration_min}–${strategy.duration_max} 秒` : "按成功交付张数计费"}</p></td>
          <td><div className="settings-price-cell">{strategy.allowed_resolutions.map(resolution => {
            const value = data.prices[strategy.id]?.[resolution] ?? "";
            return <AdminField key={resolution} label={resolution} error={priceError(value)} hint={value ? `元 / ${kind === "image" ? "张" : "秒"}` : "未配置，不可生成"}>
              <Input type="text" inputMode="decimal" disabled={busy} aria-label={`${strategy.id} ${resolution} 单价`} placeholder="未配置" value={value}
                onChange={event => { setNotice(""); setData({ ...data, prices: { ...data.prices, [strategy.id]: { ...data.prices[strategy.id], [resolution]: event.target.value } } }); }} />
            </AdminField>;
          })}</div></td>
        </tr>)}
      </tbody></table></div>
    </details>)}
    <div className="settings-price-actions">
      <Button disabled={!data || busy || invalidPrices} onClick={() => void publish()}>{operation === "publish" ? "发布中…" : "发布价格版本"}</Button>
      <Button variant="outline" disabled={busy} onClick={() => void reload()}>{operation === "load" ? "加载中…" : dirty ? "放弃修改并重载" : "重新加载"}</Button>
      {data?.active_version ? <Button variant="outline" disabled={busy} onClick={() => void toggle()}>{operation === "toggle" ? "切换中…" : data.enabled ? "停用固定计费" : "启用固定计费"}</Button> : null}
    </div>
  </SettingsPanel>;
}
