import { useCallback, useEffect, useState } from "react";
import { Check, KeyRound, Plus, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { Switch } from "@/components/ui/switch";
import { api, type AdminLogicalModel, type AdminRoutingChannel, type AdminRoutingSettings } from "@/api/client";
import { LabeledControl, SettingsLoading, SettingsPanel, SettingsTabShell } from "@/components/settings/SettingsPanel";
import { CAPABILITY_LABELS, CAPABILITY_ORDER, defaultPresetDefaults, PRESET_MODELS } from "@/lib/tokenfreeRecommendedModels";

const TOKENFREE_BASE = "https://www.tokenfree.com/v1";
const BYTEPLUS_AP = "https://ark.ap-southeast.bytepluses.com/api/v3";
const BYTEPLUS_EU = "https://ark.eu-west.bytepluses.com/api/v3";
// 火山方舟国内站，与 BytePlus ModelArk 同一套 Ark 接口
const VOLC_ARK_BJ = "https://ark.cn-beijing.volces.com/api/v3";
type Provider = "tokenfree" | "byteplus";
type Limits = { images: number; videos: number; audios: number };
const MEDIA_FIELDS = ["images", "videos", "audios"] as const;
const MEDIA_LABELS = { images: "图片 / 张", videos: "视频 / 段", audios: "音频 / 段" } as const;

// 旧单渠道配置按原默认模型显示，密钥始终仅保留已保存标记。
function editableChannels(data: AdminRoutingSettings): AdminRoutingChannel[] {
  return data.system_channels.map((channel) => ({
    ...channel,
    api_key: "",
    clear_api_key: false,
    advanced_config: channel.advanced_config ?? {
      provider: "tokenfree",
      ...defaultPresetDefaults(),
      ...(channel.enabled ? data.default_models : {}),
    },
  }));
}

// 保存后仍有 Key 的配置才入库；生效配置始终保留（与后端规则一致）
function keepOnSave(channel: AdminRoutingChannel): boolean {
  return channel.enabled || Boolean(channel.api_key.trim()) || (channel.has_api_key && !channel.clear_api_key);
}

// 多组渠道和 Key 独立保存；仅单选配置生效，不自动轮换。
export function RoutingSettingsPanel() {
  /*
   * channels 多组渠道配置（含未保存的修改）
   * models 当前生效配置下的模型及启停状态
   * limits / limitDrafts 参考素材上限（已校验值 / 输入框原文）
   */
  const [channels, setChannels] = useState<AdminRoutingChannel[]>([]);
  const [models, setModels] = useState<AdminLogicalModel[]>([]);
  const [limits, setLimits] = useState<Record<string, Limits>>({});
  const [limitDrafts, setLimitDrafts] = useState<Record<string, Partial<Record<keyof Limits, string>>>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // 已保存（线上实际生效）的配置 ID，与本地单选的 enabled 区分
  const [savedActiveId, setSavedActiveId] = useState<string | null>(null);
  // 当前展开编辑的配置 ID，只编辑不改变生效项
  const [editingId, setEditingId] = useState<string | null>(null);
  // 已入库的配置 ID，用于提示「保存后删除」
  const [savedIds, setSavedIds] = useState<string[]>([]);

  // 用服务端返回刷新列表，并默认展开生效中的配置
  const applyLoaded = useCallback((data: AdminRoutingSettings, keepEditing?: string | null) => {
    const list = editableChannels(data);
    const active = list.find((c) => c.enabled)?.id ?? null;
    setChannels(list);
    setModels(data.logical_models ?? []);
    setLimits(data.media_reference_limits ?? {});
    setLimitDrafts({});
    setSavedActiveId(active);
    setSavedIds(list.map((c) => c.id));
    setEditingId((prev) => {
      const want = keepEditing ?? prev;
      return want && list.some((c) => c.id === want) ? want : active ?? list[0]?.id ?? null;
    });
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await api<AdminRoutingSettings>("/api/admin/settings/routing");
      applyLoaded(data);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  }, [applyLoaded]);
  useEffect(() => { void load(); }, [load]);

  // 仅更新指定配置，不复制其他配置的 Key。
  function update(id: string, patch: Partial<AdminRoutingChannel>) {
    setChannels((items) => items.map((item) => item.id === id ? { ...item, ...patch } : item));
  }

  // 每个新配置使用独立 ID，一条配置对应一把 Key。
  function add(provider: Provider) {
    const byteplus = provider === "byteplus";
    const id = `${provider}-${crypto.randomUUID()}`;
    setEditingId(id);
    setChannels((items) => [...items, {
      id,
      name: byteplus ? "BytePlus" : "TokenFree",
      base_url: byteplus ? BYTEPLUS_AP : TOKENFREE_BASE,
      api_key: "", has_api_key: false, api_format: "openai", protocol: "auto",
      models: [], enabled: false, sort_order: items.length,
      advanced_config: {
        provider,
        ...(byteplus ? { text_model: "", image_model: "", video_model: "", audio_model: "" } : defaultPresetDefaults()),
      },
    }]);
  }

  // 参考上限输入校验：0–100 的整数
  const limitErrors: Record<string, Partial<Record<keyof Limits, string>>> = {};
  Object.entries(limitDrafts).forEach(([id, fields]) => {
    MEDIA_FIELDS.forEach((field) => {
      const raw = fields?.[field];
      if (raw === undefined) return;
      const value = Number(raw);
      if (!raw.trim() || !Number.isInteger(value) || value < 0 || value > 100) {
        limitErrors[id] = { ...limitErrors[id], [field]: "请输入 0–100 的整数" };
      }
    });
  });
  const limitErrorCount = Object.values(limitErrors).reduce((total, fields) => total + Object.keys(fields).length, 0);

  // 整组保存成功后重新读取脱敏配置，防止浏览器保留输入的明文 Key。
  async function save() {
    if (channels.filter((channel) => channel.enabled).length !== 1) {
      toast.error("请选择一个生效配置");
      return;
    }
    if (limitErrorCount > 0) {
      toast.error("请先修正参考素材上限");
      return;
    }
    setSaving(true);
    try {
      const res = await api<{ settings: AdminRoutingSettings }>("/api/admin/settings/routing", {
        method: "PATCH",
        body: JSON.stringify({
          system_channels: channels.filter(keepOnSave).map((channel) => ({
            ...channel, api_key: channel.api_key.trim() || undefined,
          })),
          logical_models: models,
          media_reference_limits: limits,
        }),
      });
      applyLoaded(res.settings, editingId);
      toast.success("渠道配置已保存，所选配置已生效");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  // 单选生效项（保存后才真正切换）
  function activate(id: string) {
    setChannels((items) => items.map((item) => ({ ...item, enabled: item.id === id })));
  }

  // 移除一组配置（生效中的不可删）；已入库的在保存后才从数据库删除
  function remove(id: string) {
    const target = channels.find((c) => c.id === id);
    if (!target || target.enabled) return;
    if (!window.confirm(`删除配置「${target.name || "未命名配置"}」？${savedIds.includes(id) ? "保存后生效，Key 一并删除。" : ""}`)) return;
    const rest = channels.filter((c) => c.id !== id);
    setChannels(rest);
    if (editingId === id) setEditingId(rest.find((c) => c.enabled)?.id ?? rest[0]?.id ?? null);
  }

  if (loading) return <SettingsLoading label="加载渠道配置…" />;
  const pendingActiveId = channels.find((c) => c.enabled)?.id ?? null;
  const switching = pendingActiveId !== savedActiveId;
  const channel = channels.find((c) => c.id === editingId) ?? null;
  // 保存时会被移除的配置数：手动删除的 + 未填 Key 将被丢弃的
  const dropCount = savedIds.filter((id) => !channels.some((c) => c.id === id)).length
    + channels.filter((c) => !keepOnSave(c)).length;
  return (
    <SettingsTabShell onSave={() => void save()} saving={saving} saveLabel="保存">
      <SettingsPanel title="模型渠道与 API Key" description="可保存多组 Key，但同一时间只有一组生效。点卡片查看/编辑，点「设为生效」并保存后切换。不会自动轮换或跨渠道回退；有未结束任务时暂不能修改。">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {channels.map((item) => {
            const bp = item.advanced_config?.provider === "byteplus";
            const isSaved = item.id === savedActiveId;
            const isPending = item.enabled && !isSaved;
            const isEditing = item.id === editingId;
            return (
              <div key={item.id} role="button" tabIndex={0} onClick={() => setEditingId(item.id)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setEditingId(item.id); } }}
                className={cn(
                  "cursor-pointer",
                  "relative flex flex-col gap-1.5 rounded-lg border bg-white p-3 text-left transition",
                  item.enabled ? "border-[var(--admin-forest)] bg-[#f3f8f5]" : "border-[var(--admin-border)] hover:border-[#c5d4cc]",
                  isEditing && "ring-2 ring-[var(--admin-forest)]/30",
                )}>
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{item.name || "未命名配置"}</span>
                  {isSaved && item.enabled && <span className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--admin-forest)] px-2 py-0.5 text-[11px] text-white"><Check className="h-3 w-3" />生效中</span>}
                  {isPending && <span className="ml-auto shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">保存后生效</span>}
                  {isSaved && !item.enabled && <span className="ml-auto shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-500">保存后停用</span>}
                </div>
                <div className="flex items-center gap-2 text-xs text-[#909399]">
                  <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600">{bp ? "BytePlus / 火山方舟" : "TokenFree"}</span>
                  <span className={cn("inline-flex items-center gap-1", item.has_api_key || item.api_key ? "text-[var(--admin-forest)]" : "text-red-500")}>
                    <KeyRound className="h-3 w-3" />{item.api_key ? "待保存" : item.has_api_key ? "Key 已保存" : "未填 Key"}
                  </span>
                </div>
                {isEditing && <span className="text-[11px] text-[var(--admin-forest)]">正在编辑 ↓</span>}
                {!keepOnSave(item) && <span className="text-[11px] text-amber-700">未填 Key，保存时不会保留</span>}
                {!item.enabled && (
                  <button type="button" aria-label={`删除配置 ${item.name}`} title="删除此配置"
                    className="absolute bottom-2 right-2 rounded p-1 text-[#909399] hover:bg-red-50 hover:text-red-600"
                    onClick={(e) => { e.stopPropagation(); remove(item.id); }}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="admin-btn admin-btn-secondary" type="button" onClick={() => add("tokenfree")}><Plus className="h-3.5 w-3.5" />添加 TokenFree 配置</button>
          <button className="admin-btn admin-btn-secondary" type="button" onClick={() => add("byteplus")}><Plus className="h-3.5 w-3.5" />添加 BytePlus / 火山方舟配置</button>
        </div>
        {switching && <p className="mt-3 text-xs text-amber-700">已切换生效配置，点右上角「保存」后才会应用。</p>}
        {dropCount > 0 && <p className="mt-1 text-xs text-amber-700">保存后将移除 {dropCount} 组配置（已删除或未填 Key）。</p>}
      </SettingsPanel>
      {channel && (() => {
        const advanced = channel.advanced_config!;
        const byteplus = advanced.provider === "byteplus";
        return (
          <SettingsPanel key={channel.id} title={`编辑：${channel.name || "未命名配置"}`}
            description={byteplus ? "BytePlus ModelArk / 火山方舟：填写该区域已开通的模型或接入点 ID。" : "TokenFree New API：保留现有模型预设。"}
            actions={channel.enabled
              ? <span className="inline-flex items-center gap-1 text-xs text-[var(--admin-forest)]"><Check className="h-3.5 w-3.5" />{channel.id === savedActiveId ? "当前生效配置" : "保存后生效"}</span>
              : <button type="button" className="admin-btn admin-btn-primary" onClick={() => activate(channel.id)}>设为生效</button>}>
            <div className="settings-field-grid settings-field-grid--2">
              <LabeledControl label="配置名称">
                <input className="settings-input" value={channel.name} onChange={(e) => update(channel.id, { name: e.target.value })} />
              </LabeledControl>
              <LabeledControl label={byteplus ? "服务区域" : "接口地址"}>
                {byteplus ? <select className="settings-select" value={channel.base_url} onChange={(e) => update(channel.id, { base_url: e.target.value })}>
                  <option value={BYTEPLUS_AP}>亚太（Johor）</option>
                  <option value={BYTEPLUS_EU}>欧洲（Dublin）</option>
                  <option value={VOLC_ARK_BJ}>火山方舟·国内站（北京）</option>
                </select> : <input className="settings-input" value={TOKENFREE_BASE} readOnly />}
              </LabeledControl>
              <LabeledControl label="API Key" hint={channel.has_api_key ? "已保存，留空不修改" : "未配置"} className="settings-field-span-full">
                <input className="settings-input is-secret" type="password" autoComplete="new-password"
                  placeholder={channel.has_api_key ? "已保存，留空不修改" : "粘贴此配置的 API Key"}
                  value={channel.api_key} onChange={(e) => update(channel.id, { api_key: e.target.value, clear_api_key: false })} />
                {channel.has_api_key && <label className="mt-2 flex items-center gap-2 text-xs">
                  <input type="checkbox" checked={Boolean(channel.clear_api_key)} onChange={(e) => update(channel.id, { clear_api_key: e.target.checked, api_key: "" })} />
                  保存时清除此配置的 Key
                </label>}
              </LabeledControl>
              {CAPABILITY_ORDER.map((cap) => {
                const field = `${cap}_model` as const;
                return (
                  <LabeledControl key={cap} label={`${CAPABILITY_LABELS[cap]}模型`}>
                    {byteplus ? <input className="settings-input" value={advanced[field]} disabled={cap === "audio"}
                      placeholder={cap === "audio" ? "暂不支持独立配音，可使用视频自带音频" : "填写实际模型或 ep- 接入点 ID；不用可留空"}
                      onChange={(e) => update(channel.id, { advanced_config: { ...advanced, [field]: e.target.value } })} />
                      : <select className="settings-select" value={advanced[field]} onChange={(e) => update(channel.id, { advanced_config: { ...advanced, [field]: e.target.value } })}>
                        {PRESET_MODELS[cap].map((model) => {
                          const off = channel.enabled && models.some((item) => item.id === model.id && !item.enabled);
                          return <option key={model.id} value={model.id} disabled={off}>{off ? `${model.label}（已停用）` : model.label}</option>;
                        })}
                      </select>}
                  </LabeledControl>
                );
              })}
            </div>
            {byteplus && <p className="mt-3 text-xs text-[#909399]">Key、模型和接入点必须属于所选区域；区域及可用能力以 BytePlus 或火山方舟账号控制台为准。</p>}
          </SettingsPanel>
        );
      })()}

      <SettingsPanel title="模型启停" description="当前生效配置下的模型。停用后不参与生成；默认模型被停用时，自动改用同类第一个启用的模型。切换配置后请先保存，再调整这里。">
        {models.length === 0 ? <p className="text-sm text-[var(--admin-muted)]">当前生效配置还没有可用模型。</p> : (
          <div className="settings-field-grid settings-field-grid--2">
            {CAPABILITY_ORDER.map((cap) => {
              const rows = models.filter((model) => model.capability === cap);
              if (rows.length === 0) return null;
              return (
                <section key={cap} className="space-y-2">
                  <strong className="text-sm">{CAPABILITY_LABELS[cap]}</strong>
                  {[...rows].sort((a, b) => {
                    const order = PRESET_MODELS[cap].map((row) => row.id);
                    const ia = order.indexOf(a.id), ib = order.indexOf(b.id);
                    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
                  }).map((model) => (
                    <div key={model.id} className="settings-toggle-row">
                      <div className="min-w-0"><strong>{PRESET_MODELS[cap].find((row) => row.id === model.id)?.label ?? (model.name || model.id)}</strong><span className="block break-all">{model.id}</span></div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span>{model.enabled ? "已启用" : "已停用"}</span>
                        <Switch aria-label={`${model.name || model.id} 启用`} checked={model.enabled} disabled={saving}
                          onCheckedChange={(value) => setModels((items) => items.map((item) => item.id === model.id ? { ...item, enabled: value } : item))} />
                      </div>
                    </div>
                  ))}
                </section>
              );
            })}
          </div>
        )}
      </SettingsPanel>

      {Object.keys(limits).length > 0 && (
        <details className="settings-advanced">
          <summary><span>参考素材上限</span><span>{limitErrorCount > 0 ? `${limitErrorCount} 项待修正` : "按模型限制参考图片、视频和音频数量"}</span></summary>
          <SettingsPanel title="参考素材上限" description="单次生成可携带的参考素材数量。每项为 0–100 的整数，0 表示不支持；首尾帧与画风板也计入图片数量。">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead><tr className="border-b text-xs text-[var(--admin-muted)]">
                  <th scope="col" className="px-2 py-3 font-medium">模型</th>
                  {MEDIA_FIELDS.map((field) => <th key={field} scope="col" className="px-2 py-3 font-medium">{MEDIA_LABELS[field]}</th>)}
                </tr></thead>
                <tbody>{Object.entries(limits).map(([id, row]) => (
                  <tr key={id} className="border-b last:border-0">
                    <th scope="row" className="px-2 py-3 font-medium">{[...PRESET_MODELS.image, ...PRESET_MODELS.video].find((model) => model.id === id)?.label ?? id}<code className="mt-1 block text-[10px] font-normal text-[var(--admin-muted)]">{id}</code></th>
                    {MEDIA_FIELDS.map((field) => {
                      const error = limitErrors[id]?.[field];
                      return <td key={field} className="px-2 py-3 align-top">
                        <input type="number" min="0" max="100" step="1" aria-label={`${id} ${MEDIA_LABELS[field]}上限`} aria-invalid={Boolean(error)}
                          className="settings-input !w-24" value={limitDrafts[id]?.[field] ?? row[field]} disabled={saving}
                          onChange={(event) => {
                            const raw = event.target.value;
                            setLimitDrafts((prev) => ({ ...prev, [id]: { ...prev[id], [field]: raw } }));
                            const value = Number(raw);
                            if (!raw.trim() || !Number.isInteger(value) || value < 0 || value > 100) return;
                            setLimits((prev) => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
                          }} />
                        {error ? <p className="mt-1 text-[11px] text-[#b64d43]" role="alert">{error}</p> : null}
                      </td>;
                    })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </SettingsPanel>
        </details>
      )}
    </SettingsTabShell>
  );
}
