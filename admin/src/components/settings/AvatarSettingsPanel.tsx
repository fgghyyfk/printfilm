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

/** 推荐的保脸改图模型（也可手填其它模型名） */
const IMAGE_MODEL_OPTIONS = [
  { id: "gemini-3.1-flash-image", label: "Gemini Flash Image（保脸最稳，推荐）" },
  { id: "gpt-image-2", label: "GPT Image 2（画质最好）" },
  { id: "qwen-image-edit-plus", label: "通义千问改图 Plus（便宜）" },
];

// 数字人口播：自建 GPU 推理服务、形象/场景生成服务、默认引擎与计费
export function AvatarSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();
  /* 密钥输入与连通性测试（GPU 服务 / 形象服务各一把 Key） */
  const [keyInput, setKeyInput] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [aiKeyInput, setAiKeyInput] = useState("");
  const [clearAiKey, setClearAiKey] = useState(false);
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
        avatar_ai_base_url: form.avatar_ai_base_url.trim(),
        avatar_ai_api_key: aiKeyInput.trim() || undefined,
        clear_avatar_ai_api_key: clearAiKey,
        avatar_image_model: form.avatar_image_model.trim(),
        avatar_image_size: form.avatar_image_size.trim() || "1024x1536",
        avatar_text_model: form.avatar_text_model.trim(),
      },
      "数字人配置已保存",
    );
    setKeyInput("");
    setClearKey(false);
    setAiKeyInput("");
    setClearAiKey(false);
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
            id: "ai",
            label: "形象生成",
            ready: form.has_avatar_ai_api_key && !clearAiKey,
            readyText: "已配置",
            pendingText: "未配置",
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

        <SettingsPanel
          className="settings-panel--compact"
          title="3. 形象与场景生成"
          description="只取上传照片的人脸，生成定妆照与每段场景图，并写分段口播与重点文字；OpenAI 兼容接口（默认 promptoll），不配则仍用原照片"
        >
          <div className="settings-field-grid">
            <LabeledControl label="接口地址" hint="OpenAI 兼容的 /v1 根路径" className="settings-field-span-full">
              <input
                className="settings-input"
                placeholder="https://promptoll.com/v1"
                value={form.avatar_ai_base_url}
                onChange={(e) => patchField("avatar_ai_base_url", e.target.value)}
              />
            </LabeledControl>
            <SecretField
              label="API Key"
              hint="在 promptoll 控制台创建"
              value={aiKeyInput}
              configured={form.has_avatar_ai_api_key && !clearAiKey}
              onChange={setAiKeyInput}
              onClear={() => {
                setAiKeyInput("");
                setClearAiKey(true);
              }}
            />
            <LabeledControl label="默认改图模型" hint="项目里可单独切换；需在服务端已开通该模型">
              <input
                className="settings-input"
                list="avatar-image-models"
                value={form.avatar_image_model}
                onChange={(e) => patchField("avatar_image_model", e.target.value)}
              />
              <datalist id="avatar-image-models">
                {IMAGE_MODEL_OPTIONS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </datalist>
            </LabeledControl>
            <LabeledControl label="出图尺寸" hint="竖屏建议 1024x1536；部分模型忽略此项">
              <input
                className="settings-input"
                placeholder="1024x1536"
                value={form.avatar_image_size}
                onChange={(e) => patchField("avatar_image_size", e.target.value)}
              />
            </LabeledControl>
            <LabeledControl label="文案模型" hint="写分段口播、场景描述与重点文字，如 qwen3.6-plus / kimi-k2.6">
              <input
                className="settings-input"
                value={form.avatar_text_model}
                onChange={(e) => patchField("avatar_text_model", e.target.value)}
              />
            </LabeledControl>
          </div>
        </SettingsPanel>
      </div>
    </SettingsTabShell>
  );
}
