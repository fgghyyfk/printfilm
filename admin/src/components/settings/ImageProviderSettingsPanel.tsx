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
const PROVIDER_OPTIONS = [
  { value: "promptoll", label: "Promptoll（自有网关，推荐）" },
  { value: "openai", label: "OpenAI 官方" },
];
const PROVIDER_NAME: Record<string, string> = { promptoll: "Promptoll", openai: "OpenAI" };
const SCOPE_OPTIONS = [
  { value: "tools", label: "仅工具中心（图片生成页，推荐）" },
  { value: "all", label: "全站（含短视频分镜、漫剧资产等生图）" },
];

/** 生图改走 gpt-image：渠道（Promptoll / OpenAI 官方，各自 Key）、开关、模型、质量与作用范围；主渠道继续负责文本与视频 */
export function ImageProviderSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();
  /* 两个渠道各自的 Key 输入与清除状态 */
  const [keyInput, setKeyInput] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [ptKeyInput, setPtKeyInput] = useState("");
  const [clearPtKey, setClearPtKey] = useState(false);

  // 只提交本面板字段；Key 留空表示不修改
  async function handleSave() {
    if (!form) return;
    const saved = await save({
      openai_image_enabled: form.openai_image_enabled,
      openai_image_model: form.openai_image_model,
      openai_image_quality: form.openai_image_quality,
      openai_image_scope: form.openai_image_scope,
      openai_image_provider: form.openai_image_provider,
      openai_image_api_key: clearKey ? undefined : keyInput.trim() || undefined,
      clear_openai_image_api_key: clearKey,
      promptoll_api_key: clearPtKey ? undefined : ptKeyInput.trim() || undefined,
      clear_promptoll_api_key: clearPtKey,
    }, "生图配置已保存");
    if (!saved) return;
    setKeyInput("");
    setClearKey(false);
    setPtKeyInput("");
    setClearPtKey(false);
  }

  if (loading || !form) return <SettingsLoading />;

  const provider = form.openai_image_provider || "openai";
  const providerName = PROVIDER_NAME[provider] ?? provider;
  const hasOpenaiKey = !clearKey && (form.has_openai_image_api_key || Boolean(keyInput.trim()));
  const hasPtKey = !clearPtKey && (form.has_promptoll_api_key || Boolean(ptKeyInput.trim()));
  const hasKey = provider === "promptoll" ? hasPtKey : hasOpenaiKey;
  const active = form.openai_image_enabled && hasKey;
  const scopeLabel = SCOPE_OPTIONS.find((o) => o.value === form.openai_image_scope)?.label ?? form.openai_image_scope;

  return <SettingsTabShell onSave={() => void handleSave()} saving={saving}>
    <SettingsStatusBar
      title="生图渠道"
      items={[
        { id: "provider", label: "当前生图", ready: active, readyText: `${providerName} · ${form.openai_image_model}`, pendingText: "沿用主渠道（模型页的生效配置）" },
        { id: "key", label: `${providerName} Key`, ready: hasKey, readyText: "已配置", pendingText: "未配置" },
        { id: "scope", label: "作用范围", ready: active, readyText: scopeLabel, pendingText: "未启用" },
      ]}
    />

    <SettingsPanel title="生图改走 gpt-image" description="开启后，作用范围内的生图改用 gpt-image（支持多张参考图，如「模特 + 丝巾」）；电商图里用户也可单独选「GPT Image 2」出图。文本、视频仍走「模型」页的生效配置。">
      <div className="settings-toggle-row">
        <div><strong>启用 gpt-image 生图</strong><span>{hasKey ? "关闭则恢复主渠道生图。" : `先填写 ${providerName} Key 再启用。`}</span></div>
        <Switch aria-label="启用 gpt-image 生图" checked={form.openai_image_enabled} onCheckedChange={(value) => patchField("openai_image_enabled", value)} />
      </div>
      <div className="settings-field-grid mt-5">
        <LabeledControl label="生图渠道" hint="Promptoll 是自有网关（api.promptoll.com），按实际成本回传计费；需要高画质时请用开通了官方 OpenAI 路由的 Key。">
          <SettingsSelect value={provider} options={PROVIDER_OPTIONS} onChange={(v) => patchField("openai_image_provider", v)} />
        </LabeledControl>
        {provider === "promptoll" ? <SecretField label="Promptoll API Key" placeholder="zai_…（promptoll.com 控制台）" value={ptKeyInput} configured={form.has_promptoll_api_key}
          required={form.openai_image_enabled} clearPending={clearPtKey} onUndoClear={() => setClearPtKey(false)}
          error={form.openai_image_enabled && clearPtKey ? "请先关闭生图再清除 Key，或撤销清除。" : undefined}
          onChange={(value) => { setPtKeyInput(value); setClearPtKey(false); }} onClear={() => setClearPtKey(true)} />
          : <SecretField label="OpenAI API Key" placeholder="sk-…（platform.openai.com/api-keys）" value={keyInput} configured={form.has_openai_image_api_key}
          required={form.openai_image_enabled} clearPending={clearKey} onUndoClear={() => setClearKey(false)}
          error={form.openai_image_enabled && clearKey ? "请先关闭生图再清除 Key，或撤销清除。" : undefined}
          onChange={(value) => { setKeyInput(value); setClearKey(false); }} onClear={() => setClearKey(true)} />}
        <LabeledControl label="作用范围" hint="默认只影响工具中心的图片生成；选「全站」后短视频与漫剧的生图也会改用 OpenAI。">
          <SettingsSelect value={form.openai_image_scope} options={SCOPE_OPTIONS} onChange={(v) => patchField("openai_image_scope", v)} />
        </LabeledControl>
        <LabeledControl label="模型">
          <SettingsSelect value={form.openai_image_model} options={MODEL_OPTIONS} onChange={(v) => patchField("openai_image_model", v)} />
        </LabeledControl>
        <LabeledControl label="质量" hint="质量越高越清晰，单张成本也越高（gpt-image-2 竖版官方价：中约 $0.04，高约 $0.17）。">
          <SettingsSelect value={form.openai_image_quality} options={QUALITY_OPTIONS} onChange={(v) => patchField("openai_image_quality", v)} />
        </LabeledControl>
      </div>
      <p className="mt-4 text-xs text-[#909399]">用户侧按价目表里 gpt-image-2 的张价计费；上游实际成本（Promptoll 回传）记在用量明细里，可据此在「支付计费」调价。</p>
    </SettingsPanel>
  </SettingsTabShell>;
}
