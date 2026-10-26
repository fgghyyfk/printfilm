import {
  LabeledControl,
  SettingsLoading,
  SettingsPanel,
  SettingsStatusBar,
  SettingsTabShell,
} from "@/components/settings/SettingsPanel";
import { useAdminModelSettings } from "@/hooks/useAdminModelSettings";

/** 站点公网地址与媒体工具路径 */
export function SiteSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();

  async function handleSave() {
    if (!form) return;
    await save(
      {
        public_base_url: form.public_base_url,
        ffmpeg_path: form.ffmpeg_path,
        ffprobe_path: form.ffprobe_path,
      },
      "站点配置已保存",
    );
  }

  if (loading || !form) {
    return <SettingsLoading />;
  }

  const hasPublic = Boolean(form.public_base_url?.trim());
  const hasFfmpeg = Boolean(form.ffmpeg_path?.trim());
  const hasFfprobe = Boolean(form.ffprobe_path?.trim());

  return (
    <SettingsTabShell onSave={handleSave} saving={saving} saveLabel="保存站点设置">
      <SettingsStatusBar
        title="站点工具状态"
        items={[
          {
            id: "public",
            label: "公网地址",
            ready: hasPublic,
            readyText: "已配置",
            pendingText: "未填写",
          },
          {
            id: "ffmpeg",
            label: "ffmpeg",
            ready: hasFfmpeg,
            readyText: form.ffmpeg_path || "已配置",
            pendingText: "使用默认 PATH",
          },
          {
            id: "ffprobe",
            label: "ffprobe",
            ready: hasFfprobe,
            readyText: form.ffprobe_path || "已配置",
            pendingText: "使用默认 PATH",
          },
        ]}
      />

      <div className="settings-routing-grid">
        <SettingsPanel
          className="settings-panel--compact"
          title="站点访问地址"
          description="供支付回调、分享链接和媒体地址回填使用，填写可从公网访问的后端地址。"
        >
          <div className="settings-field-grid">
            <LabeledControl
              label="后端公网基址"
              hint="例如 https://www.printfilm.com；留空使用服务器默认配置。"
              optional
              className="settings-field-span-full"
            >
              <input
                className="settings-input"
                type="url"
                placeholder="https://www.printfilm.com"
                autoComplete="url"
                value={form.public_base_url}
                onChange={(e) => patchField("public_base_url", e.target.value)}
              />
            </LabeledControl>
          </div>
          <p className="settings-panel-footnote">
            数据库、Redis、SECRET_KEY 等基础设施仍通过服务器环境变量配置，不在此页修改。
          </p>
        </SettingsPanel>

        <SettingsPanel
          className="settings-panel--compact"
          title="媒体处理工具"
          description="视频合成与抽帧使用服务器上的工具。已加入服务器 PATH 时，可保持默认名称。"
        >
          <div className="settings-field-grid">
            <LabeledControl label="视频处理工具（ffmpeg）" hint="可填写命令名或服务器上的完整路径。" optional>
              <input
                className="settings-input"
                placeholder="ffmpeg"
                value={form.ffmpeg_path}
                onChange={(e) => patchField("ffmpeg_path", e.target.value)}
              />
            </LabeledControl>
            <LabeledControl label="媒体信息工具（ffprobe）" hint="与 ffmpeg 使用同一安装目录。" optional>
              <input
                className="settings-input"
                placeholder="ffprobe"
                value={form.ffprobe_path}
                onChange={(e) => patchField("ffprobe_path", e.target.value)}
              />
            </LabeledControl>
          </div>
        </SettingsPanel>
      </div>
    </SettingsTabShell>
  );
}
