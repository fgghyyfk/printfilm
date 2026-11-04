import { useState } from "react";
import { LabeledControl, SettingsLoading, SettingsPanel, SettingsStatusBar, SettingsTabShell } from "@/components/settings/SettingsPanel";
import { SecretField } from "@/components/settings/SecretField";
import { SettingsSelect } from "@/components/settings/SettingsSelect";
import { Switch } from "@/components/ui/switch";
import { useAdminModelSettings } from "@/hooks/useAdminModelSettings";

const MODEL_OPTIONS = [
  { value: "gpt-image-2", label: "gpt-image-2（推荐）" },
  { value: "gpt-image-1", label: "gpt-image-1" },
];
const QUALITY_OPTIONS = [
  { value: "high", label: "高（推荐，商品细节更好）" },
  { value: "medium", label: "中" },
  { value: "low", label: "低（最快最省）" },
  { value: "auto", label: "自动" },
];
const SCOPE_OPTIONS = [
  { value: "tools", label: "仅工具中心（图片生成页，推荐）" },
  { value: "all", label: "全站（含短视频分镜、漫剧资产等生图）" },
];

/** 生图改走 OpenAI：开关、API Key、模型、质量与作用范围；主渠道（火山等）继续负责文本与视频 */
export function ImageProviderSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();
  const [keyInput, setKeyInput] = useState("");
  const [clearKey, setClearKey] = useState(false);

  // 只提交本面板字段；Key 留空表示不修改
  async function handleSave() {
    if (!form) return;
    const saved = await save({
      openai_image_enabled: form.openai_image_enabled,
      openai_image_model: form.openai_image_model,
      openai_image_quality: form.openai_image_quality,
      openai_image_scope: form.openai_image_scope,
      openai_image_api_key: clearKey ? undefined : keyInput.trim() || undefined,
      clear_openai_image_api_key: clearKey,
    }, "生图配置已保存");
    if (!saved) return;
    setKeyInput("");
    setClearKey(false);
  }

  if (loading || !form) return <SettingsLoading />;

  const hasKey = !clearKey && (form.has_openai_image_api_key || Boolean(keyInput.trim()));
  const active = form.openai_image_enabled && hasKey;
  const scopeLabel = SCOPE_OPTIONS.find((o) => o.value === form.openai_image_scope)?.label ?? form.openai_image_scope;

  return <SettingsTabShell onSave={() => void handleSave()} saving={saving}>
    <SettingsStatusBar
      title="生图渠道"
      items={[
        { id: "provider", label: "当前生图", ready: active, readyText: `OpenAI · ${form.openai_image_model}`, pendingText: "沿用主渠道（模型页的生效配置）" },
        { id: "key", label: "OpenAI Key", ready: hasKey, readyText: "已配置", pendingText: "未配置" },
        { id: "scope", label: "作用范围", ready: active, readyText: scopeLabel, pendingText: "未启用" },
      ]}
    />

    <SettingsPanel title="生图改走 OpenAI" description="开启后，作用范围内的生图改用 OpenAI gpt-image（支持多张参考图，如「模特 + 丝巾」）；文本、视频仍走「模型」页的生效配置。请使用 OpenAI API Key（platform.openai.com），不要使用 ChatGPT 登录凭证。">
      <div className="settings-toggle-row">
        <div><strong>启用 OpenAI 生图</strong><span>{hasKey ? "关闭则恢复主渠道生图。" : "先填写 API Key 再启用。"}</span></div>
        <Switch aria-label="启用 OpenAI 生图" checked={form.openai_image_enabled} onCheckedChange={(value) => patchField("openai_image_enabled", value)} />
      </div>
      <div className="settings-field-grid mt-5">
        <SecretField label="OpenAI API Key" placeholder="sk-…（platform.openai.com/api-keys）" value={keyInput} configured={form.has_openai_image_api_key}
          required={form.openai_image_enabled} clearPending={clearKey} onUndoClear={() => setClearKey(false)}
          error={form.openai_image_enabled && clearKey ? "请先关闭 OpenAI 生图再清除 Key，或撤销清除。" : undefined}
          onChange={(value) => { setKeyInput(value); setClearKey(false); }} onClear={() => setClearKey(true)} />
        <LabeledControl label="作用范围" hint="默认只影响工具中心的图片生成；选「全站」后短视频与漫剧的生图也会改用 OpenAI。">
          <SettingsSelect value={form.openai_image_scope} options={SCOPE_OPTIONS} onChange={(v) => patchField("openai_image_scope", v)} />
        </LabeledControl>
        <LabeledControl label="模型">
          <SettingsSelect value={form.openai_image_model} options={MODEL_OPTIONS} onChange={(v) => patchField("openai_image_model", v)} />
        </LabeledControl>
        <LabeledControl label="质量" hint="质量越高越清晰，OpenAI 侧单张成本也越高。">
          <SettingsSelect value={form.openai_image_quality} options={QUALITY_OPTIONS} onChange={(v) => patchField("openai_image_quality", v)} />
        </LabeledControl>
      </div>
      <p className="mt-4 text-xs text-[#909399]">用户侧按平台现有生图价计费（与主渠道相同）；如需按 OpenAI 成本调整，请在「支付计费」里改价目。</p>
    </SettingsPanel>
  </SettingsTabShell>;
}
