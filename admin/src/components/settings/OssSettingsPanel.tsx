import { useState } from "react";
import { LabeledControl, SettingsLoading, SettingsPanel, SettingsStatusBar, SettingsTabShell } from "@/components/settings/SettingsPanel";
import { SecretField } from "@/components/settings/SecretField";
import { Switch } from "@/components/ui/switch";
import { useAdminModelSettings } from "@/hooks/useAdminModelSettings";

export function OssSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();
  const [ossKeyIdInput, setOssKeyIdInput] = useState("");
  const [ossKeySecretInput, setOssKeySecretInput] = useState("");
  const [clearOssId, setClearOssId] = useState(false);
  const [clearOssSecret, setClearOssSecret] = useState(false);

  async function handleSave() {
    if (!form) return;
    const saved = await save({
      oss_enabled: form.oss_enabled,
      oss_endpoint: form.oss_endpoint.trim(),
      oss_region: form.oss_region.trim(),
      oss_bucket: form.oss_bucket.trim(),
      oss_folder: form.oss_folder.trim(),
      oss_public_base: form.oss_public_base.trim(),
      oss_upload_async: form.oss_upload_async,
      oss_upload_queue: form.oss_upload_queue.trim(),
      oss_access_key_id: clearOssId ? undefined : ossKeyIdInput.trim() || undefined,
      oss_access_key_secret: clearOssSecret ? undefined : ossKeySecretInput.trim() || undefined,
      clear_oss_access_key_id: clearOssId,
      clear_oss_access_key_secret: clearOssSecret,
    }, "存储配置已保存");
    if (!saved) return;
    setOssKeyIdInput("");
    setOssKeySecretInput("");
    setClearOssId(false);
    setClearOssSecret(false);
  }

  if (loading || !form) return <SettingsLoading />;

  const endpoint = form.oss_endpoint.trim().replace(/^https?:\/\//i, "").replace(/\/$/, "");
  const endpointError = form.oss_endpoint.trim() && !/^[a-z\d.-]+(?::\d+)?$/i.test(endpoint)
    ? "填写 OSS 服务域名，不包含 Bucket 名称或文件路径。" : undefined;
  const bucketError = form.oss_bucket.trim() && !/^[a-z\d-]+$/.test(form.oss_bucket.trim())
    ? "仅使用小写字母、数字和短横线；填写名称，不填写网址。" : undefined;
  const publicBase = form.oss_public_base.trim();
  let publicBaseError: string | undefined;
  if (publicBase) {
    try {
      const url = new URL(publicBase);
      if (!["https:", "http:"].includes(url.protocol) || url.search || url.hash) {
        publicBaseError = "使用 http:// 或 https:// 公网地址，不附带查询参数或锚点。";
      }
    } catch { publicBaseError = "填写完整访问地址，例如 https://cdn.example.com。"; }
  }
  const hasId = !clearOssId && (form.has_oss_access_key_id || Boolean(ossKeyIdInput.trim()));
  const hasSecret = !clearOssSecret && (form.has_oss_access_key_secret || Boolean(ossKeySecretInput.trim()));
  const ossReady = form.oss_enabled && Boolean(endpoint && !endpointError && form.oss_bucket.trim() && !bucketError && hasId && hasSecret);
  const publicEndpoint = endpoint.replace("-internal.aliyuncs.com", ".aliyuncs.com").replace("-internal-accelerate.aliyuncs.com", ".aliyuncs.com");
  const generatedBase = form.oss_bucket.trim() && endpoint && !endpointError && !bucketError ? `https://${form.oss_bucket.trim()}.${publicEndpoint}` : "";
  const accessPreview = publicBase || generatedBase;
  const folder = form.oss_folder.trim().replace(/^\/+|\/+$/g, "") || "kepu";

  return <SettingsTabShell onSave={handleSave} saving={saving} saveLabel="保存存储设置">
    <SettingsStatusBar title="存储配置状态" items={[
      { id: "oss", label: "阿里云 OSS", ready: ossReady, readyText: "配置完整", pendingText: form.oss_enabled ? "请补全连接与凭证" : "未启用" },
      { id: "public", label: "公网访问", ready: Boolean(accessPreview && !publicBaseError), readyText: publicBase ? "自定义地址" : "Bucket 默认地址", pendingText: "待配置" },
      { id: "async", label: "上传方式", ready: form.oss_enabled && form.oss_upload_async, readyText: "异步上传", pendingText: form.oss_enabled ? "同步上传" : "依赖 OSS 启用" },
    ]} extra={<span className="settings-status-extra">配置来源：{form.source === "db" ? "管理端" : "环境变量"}</span>} />

    <SettingsPanel title="存储连接" description="Endpoint 决定上传到哪里，Bucket 是存储空间名称。未启用时也可提前保存连接信息。">
      <div className="settings-toggle-row">
        <div><strong>启用阿里云 OSS</strong><span>开启后上传生成文件；关闭后使用本地静态目录。</span></div>
        <Switch aria-label="启用阿里云 OSS" checked={form.oss_enabled} onCheckedChange={(value) => patchField("oss_enabled", value)} />
      </div>
      <div className="settings-field-grid mt-5">
        <LabeledControl label="服务域名 Endpoint" required={form.oss_enabled} error={endpointError} hint="如 oss-cn-beijing.aliyuncs.com；不要填写 Bucket 访问地址。">
          <input className="settings-input" placeholder="oss-cn-beijing.aliyuncs.com" value={form.oss_endpoint} onChange={(event) => patchField("oss_endpoint", event.target.value)} autoCapitalize="none" spellCheck={false} />
        </LabeledControl>
        <LabeledControl label="存储空间 Bucket" required={form.oss_enabled} error={bucketError} hint="与 OSS 控制台名称一致，例如 printfilm-media。">
          <input className="settings-input" placeholder="printfilm-media" value={form.oss_bucket} onChange={(event) => patchField("oss_bucket", event.target.value)} autoCapitalize="none" spellCheck={false} maxLength={63} />
        </LabeledControl>
        <LabeledControl label="地域 Region" optional hint="如 cn-beijing，应与 Endpoint 所属地域一致。">
          <input className="settings-input" placeholder="cn-beijing" value={form.oss_region} onChange={(event) => patchField("oss_region", event.target.value)} autoCapitalize="none" spellCheck={false} />
        </LabeledControl>
        <LabeledControl label="文件目录前缀" optional hint="不含域名；例如 kepu。留空使用默认目录 kepu。">
          <input className="settings-input" placeholder="kepu" value={form.oss_folder} onChange={(event) => patchField("oss_folder", event.target.value)} spellCheck={false} />
        </LabeledControl>
      </div>
    </SettingsPanel>

    <SettingsPanel title="访问凭证" description="只接收新凭证，不显示已有密钥。启用 OSS 时需要完整的 AccessKey ID 和 Secret。">
      <div className="settings-field-grid">
        <SecretField label="AccessKey ID" placeholder="输入 AccessKey ID" value={ossKeyIdInput} configured={form.has_oss_access_key_id} required={form.oss_enabled}
          clearPending={clearOssId} onUndoClear={() => setClearOssId(false)}
          error={form.oss_enabled && clearOssId ? "请先关闭 OSS 再清除凭证，或撤销清除。" : undefined}
          onChange={(value) => { setOssKeyIdInput(value); setClearOssId(false); }} onClear={() => setClearOssId(true)} />
        <SecretField label="AccessKey Secret" placeholder="输入 AccessKey Secret" value={ossKeySecretInput} configured={form.has_oss_access_key_secret} required={form.oss_enabled}
          clearPending={clearOssSecret} onUndoClear={() => setClearOssSecret(false)}
          error={form.oss_enabled && clearOssSecret ? "请先关闭 OSS 再清除凭证，或撤销清除。" : undefined}
          onChange={(value) => { setOssKeySecretInput(value); setClearOssSecret(false); }} onClear={() => setClearOssSecret(true)} />
      </div>
    </SettingsPanel>

    <SettingsPanel title="公网访问与 CDN" description="浏览器通过这个地址预览和下载媒体；它与上传使用的 Endpoint 分开配置。">
      <LabeledControl label="自定义访问地址" optional error={publicBaseError} hint="绑定 CDN 或自定义域名时填写完整 https:// 地址；留空使用 Bucket 的公网地址。">
        <input className="settings-input" type="url" placeholder="https://cdn.example.com" value={form.oss_public_base} onChange={(event) => patchField("oss_public_base", event.target.value)} autoCapitalize="none" spellCheck={false} />
      </LabeledControl>
      <div className="admin-form-preview mt-4" aria-live="polite">
        <strong>媒体访问路径预览</strong>
        <div className="mt-1 break-all font-mono">{accessPreview && !publicBaseError ? `${accessPreview.replace(/\/$/, "")}/${folder}/generated/…` : "填写 Endpoint 与 Bucket 后显示默认访问路径。"}</div>
      </div>
    </SettingsPanel>

    <details className="settings-advanced">
      <summary>高级上传选项 · 上传方式与队列</summary>
      <div>
        <div className="settings-toggle-row">
          <div><strong>异步上传</strong><span>{form.oss_enabled ? "先返回本地 URL，再由后台队列上传；关闭时同步上传。" : "先启用 OSS 后可调整上传方式。"}</span></div>
          <Switch aria-label="启用异步上传" checked={form.oss_upload_async} disabled={!form.oss_enabled} onCheckedChange={(value) => patchField("oss_upload_async", value)} />
        </div>
        <div className="settings-field-grid mt-5">
          <LabeledControl label="上传队列名称" required={form.oss_enabled && form.oss_upload_async} hint="仅异步上传使用；保留与本地 Worker 配置一致的队列名称。">
            <input className="settings-input" placeholder="例如 oss_upload" value={form.oss_upload_queue} disabled={!form.oss_enabled || !form.oss_upload_async} onChange={(event) => patchField("oss_upload_queue", event.target.value)} spellCheck={false} />
          </LabeledControl>
        </div>
      </div>
    </details>
  </SettingsTabShell>;
}
