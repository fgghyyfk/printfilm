import { useState } from "react";
import { Activity } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import {
  LabeledControl,
  SettingsLoading,
  SettingsPanel,
  SettingsStatusBar,
  SettingsTabShell,
} from "@/components/settings/SettingsPanel";
import { SecretField } from "@/components/settings/SecretField";
import { useAdminModelSettings } from "@/hooks/useAdminModelSettings";

/** 数字人服务连通性（/api/avatar/status 返回） */
type AvatarStatus = {
  configured: boolean;
  online: boolean;
  mock: boolean;
  engines: string[];
  active_engine?: string | null;
  queued?: number;
};

// 数字人口播：自建 GPU 推理服务地址、Key、默认引擎与计费
export function AvatarSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();
  /* 密钥输入与连通性测试 */
  const [keyInput, setKeyInput] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [checking, setChecking] = useState(false);
  const [status, setStatus] = useState<AvatarStatus | null>(null);

  async function handleSave() {
    if (!form) return;
    await save(
      {
        avatar_base_url: form.avatar_base_url.trim(),
        avatar_api_key: keyInput.trim() || undefined,
        clear_avatar_api_key: clearKey,
        avatar_engine: form.avatar_engine,
        avatar_resolution: form.avatar_resolution,
        avatar_poll_interval: Number(form.avatar_poll_interval),
        avatar_poll_timeout: Number(form.avatar_poll_timeout),
        avatar_segment_max_sec: Number(form.avatar_segment_max_sec),
        avatar_price_fen_per_sec: Number(form.avatar_price_fen_per_sec),
      },
      "数字人配置已保存",
    );
    setKeyInput("");
    setClearKey(false);
    setStatus(null);
  }

  // 用已保存的配置探测服务
  async function checkService() {
    setChecking(true);
    try {
      const s = await api<AvatarStatus>("/api/avatar/status");
      setStatus(s);
      if (s.online) toast.success(s.mock ? "当前为模拟模式（ARK_MOCK）" : "数字人服务在线");
      else toast.error(s.configured ? "服务连不上，请检查地址与 GPU 机器" : "尚未填写服务地址");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "检测失败");
    } finally {
      setChecking(false);
    }
  }

  if (loading || !form) {
    return <SettingsLoading />;
  }

  const configured = Boolean(form.avatar_base_url.trim());

  return (
    <SettingsTabShell onSave={() => void handleSave()} saving={saving}>
      <SettingsStatusBar
        title="数字人服务"
        items={[
          {
            id: "url",
            label: "服务地址",
            ready: configured,
            readyText: "已填写",
            pendingText: "未填写",
          },
          {
            id: "online",
            label: "连通性",
            ready: Boolean(status?.online),
            readyText: status?.mock ? "模拟模式" : "在线",
            pendingText: status ? "连不上" : "未检测",
          },
        ]}
        extra={
          <button
            type="button"
            className="admin-btn admin-btn-secondary settings-mini-btn"
            disabled={checking}
            onClick={() => void checkService()}
          >
            <Activity className="h-3.5 w-3.5" />
            {checking ? "检测中…" : "测试连接"}
          </button>
        }
      />

      <div className="settings-routing-grid">
        <SettingsPanel
          className="settings-panel--compact"
          title="1. GPU 推理服务"
          description="部署见 deploy/avatar-gpu/server/README.md；修改后先保存再测试连接"
        >
          <div className="settings-field-grid">
            <LabeledControl
              label="服务地址"
              hint="如 http://10.10.1.84:8780；需要 API 服务器能访问到"
              className="settings-field-span-full"
            >
              <input
                className="settings-input"
                placeholder="http://gpu-host:8780"
                value={form.avatar_base_url}
                onChange={(e) => patchField("avatar_base_url", e.target.value)}
              />
            </LabeledControl>
            <SecretField
              label="API Key"
              hint="与 GPU 服务启动时的 AVATAR_API_KEY 一致"
              value={keyInput}
              configured={form.has_avatar_api_key && !clearKey}
              onChange={setKeyInput}
              onClear={() => {
                setKeyInput("");
                setClearKey(true);
              }}
            />
            <LabeledControl label="默认引擎" hint="项目可单独指定；切换引擎时 GPU 需重新加载约 15 分钟">
              <select
                className="settings-select"
                value={form.avatar_engine}
                onChange={(e) => patchField("avatar_engine", e.target.value as "infinitetalk" | "longcat")}
              >
                <option value="infinitetalk">InfiniteTalk（推荐）</option>
                <option value="longcat">LongCat 1.5</option>
              </select>
            </LabeledControl>
            <LabeledControl label="分辨率" hint="24GB 显卡建议 480p">
              <select
                className="settings-select"
                value={form.avatar_resolution}
                onChange={(e) => patchField("avatar_resolution", e.target.value as "480p" | "720p")}
              >
                <option value="480p">480p</option>
                <option value="720p">720p</option>
              </select>
            </LabeledControl>
          </div>
        </SettingsPanel>

        <SettingsPanel
          className="settings-panel--compact"
          title="2. 生成与计费"
          description="口播按段生成；单卡 3090 每 15 秒片段约 30 分钟"
        >
          <div className="settings-field-grid">
            <LabeledControl label="每段最长（秒）" hint="6–30；越长单段越慢，失败重做代价越大">
              <input
                type="number"
                className="settings-input"
                min={6}
                max={30}
                value={form.avatar_segment_max_sec}
                onChange={(e) => patchField("avatar_segment_max_sec", Number(e.target.value))}
              />
            </LabeledControl>
            <LabeledControl label="轮询间隔（秒）">
              <input
                type="number"
                className="settings-input"
                min={5}
                max={600}
                value={form.avatar_poll_interval}
                onChange={(e) => patchField("avatar_poll_interval", Number(e.target.value))}
              />
            </LabeledControl>
            <LabeledControl label="单段超时（秒）" hint="含排队时间，默认 4 小时">
              <input
                type="number"
                className="settings-input"
                min={600}
                max={86400}
                value={form.avatar_poll_timeout}
                onChange={(e) => patchField("avatar_poll_timeout", Number(e.target.value))}
              />
            </LabeledControl>
            <LabeledControl label="单价（分/秒成片）" hint="自建 GPU 默认 0 不收费；开启计费时按成片秒数扣费">
              <input
                type="number"
                className="settings-input"
                min={0}
                value={form.avatar_price_fen_per_sec}
                onChange={(e) => patchField("avatar_price_fen_per_sec", Number(e.target.value))}
              />
            </LabeledControl>
          </div>
        </SettingsPanel>
      </div>
    </SettingsTabShell>
  );
}
