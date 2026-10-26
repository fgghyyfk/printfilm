import { Select } from "@/components/ui/select";
import { useCallback, useEffect, useState } from "react";
import { Eye, EyeOff, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { api, type AdminRoutingSettings } from "@/api/client";
import {
  LabeledControl,
  SettingsLoading,
  SettingsPanel,
  SettingsSurface,
  SettingsTabShell,
} from "@/components/settings/SettingsPanel";
import {
  allPresetChannelModels,
  CAPABILITY_LABELS,
  CAPABILITY_ORDER,
  clampDefaultToPreset,
  defaultPresetDefaults,
  detectDefaultRemaps,
  PRESET_MODELS,
  type ModelCapability,
} from "@/lib/tokenfreeRecommendedModels";
import { cn } from "@/lib/utils";

const TOKENFREE_CHANNEL_ID = "tokenfree";
const TOKENFREE_BASE_URL = "https://www.tokenfree.com/v1";
const TOKENFREE_CONSOLE_URL = "https://www.tokenfree.com/channels";

const DEFAULT_KEYS = ["text_model", "image_model", "video_model", "audio_model"] as const;
const MEDIA_FIELDS = ["images", "videos", "audios"] as const;
type MediaField = (typeof MEDIA_FIELDS)[number];
const MEDIA_LABELS: Record<MediaField, string> = { images: "参考图片", videos: "参考视频", audios: "参考音频" };

function buildReadiness(data: AdminRoutingSettings | null, hasKey: boolean) {
  const defaults = data?.default_models;
  return [
    { id: "secret", label: "API Key", ready: hasKey },
    { id: "text", label: "文本模型", ready: Boolean(defaults?.text_model) },
    { id: "image", label: "图像模型", ready: Boolean(defaults?.image_model) },
    { id: "video", label: "视频模型", ready: Boolean(defaults?.video_model) },
    { id: "audio", label: "配音模型", ready: Boolean(defaults?.audio_model) },
  ] as const;
}

function normalizeDefaults(raw: AdminRoutingSettings["default_models"] | undefined) {
  const base = defaultPresetDefaults();
  return {
    text_model: clampDefaultToPreset("text", raw?.text_model || base.text_model),
    image_model: clampDefaultToPreset("image", raw?.image_model || base.image_model),
    video_model: clampDefaultToPreset("video", raw?.video_model || base.video_model),
    audio_model: clampDefaultToPreset("audio", raw?.audio_model || base.audio_model),
  };
}

// 开源版模型配置：固定 TokenFree + 四类预设下拉
export function RoutingSettingsPanel() {
  const [data, setData] = useState<AdminRoutingSettings | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [referenceDrafts, setReferenceDrafts] = useState<Record<string, Partial<Record<MediaField, string>>>>({});
  const [defaultRemaps, setDefaultRemaps] = useState<
    Array<{ capability: ModelCapability; from: string; to: string }>
  >([]);

  const channel = data?.system_channels.find((item) => item.id === TOKENFREE_CHANNEL_ID) ?? data?.system_channels[0];
  const hasSavedKey = Boolean(channel?.has_api_key);
  const hasKey = hasSavedKey || Boolean(apiKeyInput.trim());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<AdminRoutingSettings>("/api/admin/settings/routing");
      setDefaultRemaps(detectDefaultRemaps(res.default_models));
      setData({
        ...res,
        default_models: normalizeDefaults(res.default_models),
        system_channels: res.system_channels.map((item) =>
          item.id === TOKENFREE_CHANNEL_ID
            ? { ...item, models: allPresetChannelModels() }
            : item,
        ),
      });
      setApiKeyInput("");
      setReferenceDrafts({});
      setShowKey(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载模型配置失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const readiness = buildReadiness(data, hasKey);
  const referenceErrors: Record<string, Partial<Record<MediaField, string>>> = {};
  Object.entries(data?.media_reference_limits ?? {}).forEach(([id, limits]) => {
    MEDIA_FIELDS.forEach((field) => {
      if (PRESET_MODELS.image.some((model) => model.id === id) && field !== "images") return;
      const raw = referenceDrafts[id]?.[field] ?? String(limits[field]);
      const value = Number(raw);
      if (!raw.trim() || !Number.isInteger(value) || value < 0 || value > 100) {
        referenceErrors[id] = { ...referenceErrors[id], [field]: "请输入 0–100 的整数" };
      }
    });
  });
  const referenceErrorCount = Object.values(referenceErrors).reduce((total, fields) => total + Object.keys(fields).length, 0);
  const keyError = hasKey ? undefined : "请填写 API Key，保存后即可调用模型。";

  function setDefaultModel(key: (typeof DEFAULT_KEYS)[number], value: string) {
    const cap = key.replace("_model", "") as ModelCapability;
    const next = clampDefaultToPreset(cap, value);
    setData((prev) =>
      prev
        ? {
            ...prev,
            default_models: { ...prev.default_models, [key]: next },
          }
        : prev,
    );
  }

  async function handleSave() {
    if (!data || saving || keyError || referenceErrorCount > 0) return;
    setSaving(true);
    try {
      const defaults = normalizeDefaults(data.default_models);
      const res = await api<{ settings: AdminRoutingSettings }>("/api/admin/settings/routing", {
        method: "PATCH",
        body: JSON.stringify({
          system_channels: [
            {
              id: TOKENFREE_CHANNEL_ID,
              name: "TokenFree New API",
              base_url: TOKENFREE_BASE_URL,
              api_key: apiKeyInput.trim() || undefined,
              api_format: "openai",
              protocol: "auto",
              models: allPresetChannelModels(),
              enabled: true,
              sort_order: 0,
            },
          ],
          default_models: defaults,
          media_reference_limits: data.media_reference_limits,
        }),
      });
      setData({
        ...res.settings,
        default_models: normalizeDefaults(res.settings.default_models),
      });
      setDefaultRemaps([]);
      setApiKeyInput("");
      setReferenceDrafts({});
      setShowKey(false);
      toast.success("模型配置已保存");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading && !data) {
    return <SettingsLoading label="加载模型配置…" />;
  }

  if (!data) return <SettingsSurface><p>模型配置加载失败。</p><button type="button" className="admin-btn admin-btn-secondary mt-3" onClick={() => void load()}>重新加载</button></SettingsSurface>;

  return (
    <SettingsTabShell onSave={handleSave} saving={saving} disabled={Boolean(keyError) || referenceErrorCount > 0} saveLabel="保存模型设置">
      <SettingsSurface className="settings-readiness-bar">
        <div className="settings-readiness-title">配置就绪</div>
        <div className="settings-readiness-row">
          {readiness.map((item) => (
            <div key={item.id} className={cn("settings-readiness-item", item.ready && "is-ready")}>
              <span className={cn("settings-readiness-dot", item.ready ? "is-on" : "is-off")} />
              <span>{item.label}</span>
              <em>{item.ready ? "已配置" : "未就绪"}</em>
            </div>
          ))}
        </div>
      </SettingsSurface>

      {(data?.validation_errors.length ?? 0) > 0 ? (
        <SettingsSurface className="border-[#fde2e2] bg-[#fef0f0]">
          <div className="text-xs font-medium text-[#f56c6c]">配置校验</div>
          <ul className="mt-1 space-y-0.5 text-xs text-[#f56c6c]">
            {data?.validation_errors.map((item) => (
              <li key={item}>· {item}</li>
            ))}
          </ul>
        </SettingsSurface>
      ) : null}

      {defaultRemaps.length > 0 ? (
        <SettingsSurface className="border-[#faecd8] bg-[#fdf6ec]">
          <div className="text-xs font-medium text-[#e6a23c]">默认模型已映射到预设</div>
          <ul className="mt-1 space-y-0.5 text-xs text-[#b88230]">
            {defaultRemaps.map((item) => (
              <li key={`${item.capability}-${item.from}`}>
                · {CAPABILITY_LABELS[item.capability]}：
                <code className="mx-1">{item.from}</code>→
                <code className="mx-1">{item.to}</code>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-[#909399]">保存后将写入上述预设 id；若需保留历史模型请先改下拉再保存。</p>
        </SettingsSurface>
      ) : null}

      <SettingsPanel title="连接模型服务" description="使用 TokenFree API Key 连接上游。接口地址已预设，完成授权后再选择默认模型。">
        <LabeledControl label="TokenFree API Key" required={!hasSavedKey} optional={hasSavedKey} error={keyError}
          hint={hasSavedKey ? "已保存密钥。留空会保留原值；输入新密钥将在保存后替换。" : "在 TokenFree 控制台创建密钥并粘贴到此处。"}>
          <div className="settings-secret-row">
            <input className="settings-input is-secret" type={showKey ? "text" : "password"}
              autoComplete="new-password" autoCapitalize="off" spellCheck={false}
              required={!hasSavedKey} aria-invalid={Boolean(keyError)}
              placeholder={hasSavedKey ? "已保存，留空保留当前密钥" : "粘贴 API Key"}
              value={apiKeyInput} onChange={(e) => setApiKeyInput(e.target.value)} />
            <button type="button" className="admin-btn admin-btn-secondary settings-mini-btn" aria-label={showKey ? "隐藏 API Key" : "显示 API Key"} aria-pressed={showKey} onClick={() => setShowKey((value) => !value)}>
              {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
            {apiKeyInput ? <button type="button" className="admin-btn admin-btn-secondary settings-mini-btn" onClick={() => { setApiKeyInput(""); setShowKey(false); }}>撤销输入</button> : null}
          </div>
        </LabeledControl>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--admin-muted)]">
          <span>连接地址 <code className="ml-1 text-[var(--admin-text)]">{TOKENFREE_BASE_URL}</code></span>
          <a className="inline-flex items-center gap-1.5 font-medium text-[var(--admin-forest)] hover:underline" href={TOKENFREE_CONSOLE_URL} target="_blank" rel="noreferrer">打开 TokenFree 控制台<ExternalLink className="h-3.5 w-3.5" /></a>
        </div>
      </SettingsPanel>

      <SettingsPanel title="默认生成模型" description="为每类任务选择站点默认模型。用户创作时仍可切换到其它预设模型。">
        <div className="settings-field-grid settings-field-grid--2">
          {CAPABILITY_ORDER.map((cap) => {
            const key = `${cap}_model` as (typeof DEFAULT_KEYS)[number];
            const options = PRESET_MODELS[cap];
            const hints: Record<ModelCapability, string> = { text: "用于脚本、摘要与分镜规划。", image: "用于角色、场景、道具等参考图生成。", video: "用于分镜视频生成。", audio: "用于角色配音；当前提供一款预设模型。" };
            return (
              <LabeledControl key={key} label={`${CAPABILITY_LABELS[cap]}模型`} hint={hints[cap]} required>
                <Select className="settings-select" value={data.default_models[key] ?? options[0]?.id ?? ""} required
                  aria-label={`${CAPABILITY_LABELS[cap]}默认模型`} onChange={(e) => setDefaultModel(key, e.target.value)}>
                  {options.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}
                </Select>
              </LabeledControl>
            );
          })}
        </div>
      </SettingsPanel>

      <details className="settings-advanced">
        <summary><span>高级模型设置</span><span>{referenceErrorCount > 0 ? `${referenceErrorCount} 项参考上限待修正` : "按模型配置参考素材数量"}</span></summary>
        <SettingsPanel title="参考素材上限" description="限制单次生成可携带的参考图片、视频和音频数量。每项为 0–100 的整数，0 表示不支持；首尾帧与画风板也计入图片数量。">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b text-xs text-[var(--admin-muted)]">
                <th scope="col" className="px-2 py-3 font-medium">模型</th>
                <th scope="col" className="px-2 py-3 font-medium">图片 / 张</th>
                <th scope="col" className="px-2 py-3 font-medium">视频 / 段</th>
                <th scope="col" className="px-2 py-3 font-medium">音频 / 段</th>
              </tr></thead>
              <tbody>{Object.entries(data.media_reference_limits ?? {}).map(([id, limits]) => (
                <tr key={id} className="border-b last:border-0">
                  <th scope="row" className="px-2 py-3 font-medium">{[...PRESET_MODELS.image, ...PRESET_MODELS.video].find((model) => model.id === id)?.label ?? id}<code className="mt-1 block text-[10px] font-normal text-[var(--admin-muted)]">{id}</code></th>
                  {MEDIA_FIELDS.map((field) => {
                    const unsupported = PRESET_MODELS.image.some((model) => model.id === id) && field !== "images";
                    const error = referenceErrors[id]?.[field];
                    return <td key={field} className="px-2 py-3 align-top">
                      {unsupported ? <span className="inline-flex h-10 items-center text-xs text-[var(--admin-muted)]">不支持</span> : <>
                        <input type="number" min="0" max="100" step="1" required
                          aria-label={`${id} ${MEDIA_LABELS[field]}上限`} aria-invalid={Boolean(error)}
                          className="settings-input !w-24" value={referenceDrafts[id]?.[field] ?? limits[field]} disabled={saving}
                          onChange={(event) => {
                            const raw = event.target.value;
                            setReferenceDrafts((prev) => ({ ...prev, [id]: { ...prev[id], [field]: raw } }));
                            const value = Number(raw);
                            if (!raw.trim() || !Number.isInteger(value) || value < 0 || value > 100) return;
                            setData((prev) => prev ? { ...prev, media_reference_limits: { ...prev.media_reference_limits,
                              [id]: { ...prev.media_reference_limits[id], [field]: value } } } : prev);
                          }} />
                        {error ? <p className="mt-1 text-[11px] text-[#b64d43]" role="alert">{error}</p> : null}
                      </>}
                    </td>;
                  })}
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="mt-4 text-xs leading-6 text-[var(--admin-muted)]">这些值用于提交任务前的数量校验。生成图片的模型仅接受图片参考；实际可用能力以对应上游模型为准。</p>
        </SettingsPanel>
      </details>
    </SettingsTabShell>
  );
}
