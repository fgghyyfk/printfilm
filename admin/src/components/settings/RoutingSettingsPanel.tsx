import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { api, type AdminRoutingChannel, type AdminRoutingSettings } from "@/api/client";
import { LabeledControl, SettingsLoading, SettingsPanel, SettingsTabShell } from "@/components/settings/SettingsPanel";
import { CAPABILITY_LABELS, CAPABILITY_ORDER, defaultPresetDefaults, PRESET_MODELS } from "@/lib/tokenfreeRecommendedModels";

const TOKENFREE_BASE = "https://www.tokenfree.com/v1";
const BYTEPLUS_AP = "https://ark.ap-southeast.bytepluses.com/api/v3";
const BYTEPLUS_EU = "https://ark.eu-west.bytepluses.com/api/v3";
type Provider = "tokenfree" | "byteplus";

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

// 多组渠道和 Key 独立保存；仅单选配置生效，不自动轮换。
export function RoutingSettingsPanel() {
  const [channels, setChannels] = useState<AdminRoutingChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const load = useCallback(async () => {
    try {
      const data = await api<AdminRoutingSettings>("/api/admin/settings/routing");
      setChannels(editableChannels(data));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  // 仅更新指定配置，不复制其他配置的 Key。
  function update(id: string, patch: Partial<AdminRoutingChannel>) {
    setChannels((items) => items.map((item) => item.id === id ? { ...item, ...patch } : item));
  }

  // 每个新配置使用独立 ID，一条配置对应一把 Key。
  function add(provider: Provider) {
    const byteplus = provider === "byteplus";
    setChannels((items) => [...items, {
      id: `${provider}-${crypto.randomUUID()}`,
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

  // 整组保存成功后重新读取脱敏配置，防止浏览器保留输入的明文 Key。
  async function save() {
    if (channels.filter((channel) => channel.enabled).length !== 1) {
      toast.error("请选择一个生效配置");
      return;
    }
    setSaving(true);
    try {
      const res = await api<{ settings: AdminRoutingSettings }>("/api/admin/settings/routing", {
        method: "PATCH",
        body: JSON.stringify({ system_channels: channels.map((channel) => ({
          ...channel, api_key: channel.api_key.trim() || undefined,
        })) }),
      });
      setChannels(editableChannels(res.settings));
      toast.success("渠道配置已保存，所选配置已生效");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <SettingsLoading label="加载渠道配置…" />;
  return (
    <SettingsTabShell onSave={() => void save()} saving={saving} saveLabel="保存">
      <SettingsPanel title="模型渠道与 API Key" description="可保存多组 Key，手动选择一组并保存后生效。不会自动轮换或跨渠道回退；有未结束任务时暂不能修改。">
        <div className="flex gap-2">
          <button className="admin-btn admin-btn-secondary" type="button" onClick={() => add("tokenfree")}>添加 TokenFree 配置</button>
          <button className="admin-btn admin-btn-secondary" type="button" onClick={() => add("byteplus")}>添加 BytePlus 配置</button>
        </div>
      </SettingsPanel>
      {channels.map((channel) => {
        const advanced = channel.advanced_config!;
        const byteplus = advanced.provider === "byteplus";
        return (
          <SettingsPanel key={channel.id} title={channel.name} description={byteplus ? "BytePlus ModelArk：填写该区域已开通的模型或接入点 ID。" : "TokenFree New API：保留现有模型预设。"}>
            <label className="mb-4 flex items-center gap-2">
              <input type="radio" name="active-channel" checked={channel.enabled}
                onChange={() => setChannels((items) => items.map((item) => ({ ...item, enabled: item.id === channel.id })))} />
              选择此配置生效（保存后应用）
            </label>
            <div className="settings-field-grid settings-field-grid--2">
              <LabeledControl label="配置名称">
                <input className="settings-input" value={channel.name} onChange={(e) => update(channel.id, { name: e.target.value })} />
              </LabeledControl>
              <LabeledControl label={byteplus ? "服务区域" : "接口地址"}>
                {byteplus ? <select className="settings-select" value={channel.base_url} onChange={(e) => update(channel.id, { base_url: e.target.value })}>
                  <option value={BYTEPLUS_AP}>亚太（Johor）</option>
                  <option value={BYTEPLUS_EU}>欧洲（Dublin）</option>
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
                        {PRESET_MODELS[cap].map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                      </select>}
                  </LabeledControl>
                );
              })}
            </div>
            {byteplus && <p className="mt-3 text-xs text-[#909399]">Key、模型和接入点必须属于所选区域；区域及可用能力以 BytePlus 账号控制台为准。</p>}
          </SettingsPanel>
        );
      })}
    </SettingsTabShell>
  );
}
