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
const VIDEO_MODEL_OPTIONS = [
  { value: "seedance-2.0", label: "Seedance 2.0（推荐）" },
  { value: "seedance-2.0-fast", label: "Seedance 2.0 Fast（更快更省）" },
  { value: "seedance-2.5", label: "Seedance 2.5（最长 30 秒，最贵）" },
];
const SCOPE_OPTIONS = [
  { value: "tools", label: "仅工具中心（图片生成页，推荐）" },
  { value: "all", label: "全站（含短视频分镜、漫剧资产等生图）" },
];

/** 生图改走 gpt-image：渠道（Promptoll / OpenAI 官方，各自 Key）、开关、模型、质量与作用范围；另可让视频改走 Promptoll 的 Seedance */
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
      promptoll_video_enabled: form.promptoll_video_enabled,
      promptoll_video_default: form.promptoll_video_default,
    }, "生图与视频配置已保存");
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
  const videoActive = form.promptoll_video_enabled && hasPtKey;
  const scopeLabel = SCOPE_OPTIONS.find((o) => o.value === form.openai_image_scope)?.label ?? form.openai_image_scope;

  return <SettingsTabShell onSave={() => void handleSave()} saving={saving}>
    <SettingsStatusBar
      title="生图渠道"
      items={[
        { id: "provider", label: "当前生图", ready: active, readyText: `${providerName} · ${form.openai_image_model}`, pendingText: "沿用主渠道（模型页的生效配置）" },
        { id: "key", label: `${providerName} Key`, ready: hasKey, readyText: "已配置", pendingText: "未配置" },
        { id: "scope", label: "作用范围", ready: active, readyText: scopeLabel, pendingText: "未启用" },
        { id: "video", label: "视频", ready: videoActive, readyText: "Promptoll · Seedance", pendingText: "沿用主渠道" },
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

    <SettingsPanel title="视频改走 Promptoll（Seedance）" description="开启后漫剧分镜、短视频与视频生成页可选 Seedance 2.0 / 2.0 Fast / 2.5，经 Promptoll 网关出片；多参考图、首尾帧、参考音频与尾帧续接与方舟一致。主渠道已配视频模型时以主渠道为准。">
      <div className="settings-toggle-row">
        <div><strong>启用 Promptoll 视频</strong><span>{hasPtKey ? "复用上方的 Promptoll Key；关闭则恢复主渠道视频。" : "先把生图渠道切到 Promptoll 并填写 Key。"}</span></div>
        <Switch aria-label="启用 Promptoll 视频" checked={form.promptoll_video_enabled} onCheckedChange={(value) => patchField("promptoll_video_enabled", value)} />
      </div>
      <div className="settings-field-grid mt-5">
        <LabeledControl label="默认视频模型" hint="主渠道没有视频模型时作为站点默认；用户仍可在生成时切换。">
          <SettingsSelect value={form.promptoll_video_default || "seedance-2.0"} options={VIDEO_MODEL_OPTIONS} onChange={(v) => patchField("promptoll_video_default", v)} />
        </LabeledControl>
      </div>
      <p className="mt-4 text-xs text-[#909399]">用户侧按「支付计费 → 图片与视频价格」里 Seedance 的秒价计费；Seedance 不接受真人人脸参考图（需走上游素材库），真人照片会被拒绝。</p>
    </SettingsPanel>
  </SettingsTabShell>;
}
