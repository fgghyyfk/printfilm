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
import { SettingsSelect } from "@/components/settings/SettingsSelect";
import {
  AVATAR_AI_BASE_OPTIONS,
  AVATAR_IMAGE_MODEL_OPTIONS,
  AVATAR_IMAGE_SIZE_OPTIONS,
  AVATAR_POLL_INTERVAL_OPTIONS,
  AVATAR_POLL_TIMEOUT_OPTIONS,
  AVATAR_PRICE_OPTIONS,
  AVATAR_SEGMENT_OPTIONS,
  AVATAR_TEXT_MODEL_OPTIONS,
  VOLC_INSTRUCTION_OPTIONS,
  VOLC_PRICE_OPTIONS,
  VOLC_RESOURCE_OPTIONS,
  VOLC_SPEECH_RATE_OPTIONS,
} from "@/lib/settingsOptions";
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
// 数字人口播：自建 GPU 推理服务、形象/场景生成服务、默认引擎与计费
export function AvatarSettingsPanel() {
  const { form, loading, saving, patchField, save } = useAdminModelSettings();
  /* 密钥输入与连通性测试（GPU 服务 / 形象服务各一把 Key） */
  const [keyInput, setKeyInput] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [aiKeyInput, setAiKeyInput] = useState("");
  const [clearAiKey, setClearAiKey] = useState(false);
  const [volcKeyInput, setVolcKeyInput] = useState("");
  const [clearVolcKey, setClearVolcKey] = useState(false);
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
        volc_tts_base_url: form.volc_tts_base_url.trim(),
        volc_tts_api_key: volcKeyInput.trim() || undefined,
        clear_volc_tts_api_key: clearVolcKey,
        volc_tts_resource_id: form.volc_tts_resource_id.trim() || "seed-tts-1.0",
        volc_tts_speech_rate: Number(form.volc_tts_speech_rate),
        volc_tts_instruction: form.volc_tts_instruction.trim(),
        volc_tts_fen_per_10k_chars: Number(form.volc_tts_fen_per_10k_chars),
      },
      "数字人配置已保存",
    );
    setKeyInput("");
    setClearKey(false);
    setAiKeyInput("");
    setClearAiKey(false);
    setVolcKeyInput("");
    setClearVolcKey(false);
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
            <LabeledControl label="每段最长（秒）" hint="越长单段越慢，失败重做代价越大">
              <SettingsSelect value={form.avatar_segment_max_sec} options={AVATAR_SEGMENT_OPTIONS} onChange={(v) => patchField("avatar_segment_max_sec", v)} />
            </LabeledControl>
            <LabeledControl label="轮询间隔（秒）">
              <SettingsSelect value={form.avatar_poll_interval} options={AVATAR_POLL_INTERVAL_OPTIONS} onChange={(v) => patchField("avatar_poll_interval", v)} />
            </LabeledControl>
            <LabeledControl label="单段超时（秒）" hint="含排队时间，默认 4 小时">
              <SettingsSelect value={form.avatar_poll_timeout} options={AVATAR_POLL_TIMEOUT_OPTIONS} onChange={(v) => patchField("avatar_poll_timeout", v)} />
            </LabeledControl>
            <LabeledControl label="单价（分/秒成片）" hint="自建 GPU 默认 0 不收费；开启计费时按成片秒数扣费">
              <SettingsSelect value={form.avatar_price_fen_per_sec} options={AVATAR_PRICE_OPTIONS} onChange={(v) => patchField("avatar_price_fen_per_sec", v)} />
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
              <SettingsSelect value={form.avatar_ai_base_url} options={AVATAR_AI_BASE_OPTIONS} onChange={(v) => patchField("avatar_ai_base_url", v)} />
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
              <SettingsSelect value={form.avatar_image_model} options={AVATAR_IMAGE_MODEL_OPTIONS} onChange={(v) => patchField("avatar_image_model", v)} />
            </LabeledControl>
            <LabeledControl label="出图尺寸" hint="部分模型忽略此项">
              <SettingsSelect value={form.avatar_image_size} options={AVATAR_IMAGE_SIZE_OPTIONS} onChange={(v) => patchField("avatar_image_size", v)} />
            </LabeledControl>
            <LabeledControl label="文案模型" hint="写分段口播、场景描述与重点文字">
              <SettingsSelect value={form.avatar_text_model} options={AVATAR_TEXT_MODEL_OPTIONS} onChange={(v) => patchField("avatar_text_model", v)} />
            </LabeledControl>
          </div>
        </SettingsPanel>

        <SettingsPanel
          className="settings-panel--compact"
          title="4. 配音（火山引擎豆包）"
          description="配了 Key 后，豆包音色（*_bigtts、ICL_*）直连火山引擎语音合成；2.0 音色可加语气指令。不配则走原有渠道或微软免费音色"
        >
          <div className="settings-field-grid">
            <SecretField
              label="API Key"
              hint="火山引擎控制台 → 豆包语音 → API Key 管理"
              value={volcKeyInput}
              configured={form.has_volc_tts_api_key && !clearVolcKey}
              onChange={setVolcKeyInput}
              onClear={() => {
                setVolcKeyInput("");
                setClearVolcKey(true);
              }}
            />
            <LabeledControl label="语速" hint="口播一般稍快一点更有节奏">
              <SettingsSelect value={form.volc_tts_speech_rate} options={VOLC_SPEECH_RATE_OPTIONS} onChange={(v) => patchField("volc_tts_speech_rate", v)} />
            </LabeledControl>
            <LabeledControl label="语气指令（2.0 音色）" hint="只对豆包 2.0 音色生效" className="settings-field-span-full">
              <SettingsSelect value={form.volc_tts_instruction} options={VOLC_INSTRUCTION_OPTIONS} onChange={(v) => patchField("volc_tts_instruction", v)} />
            </LabeledControl>
            <LabeledControl label="1.0 音色资源" hint="非 2.0 音色用；2.0 音色自动用 seed-tts-2.0">
              <SettingsSelect value={form.volc_tts_resource_id} options={VOLC_RESOURCE_OPTIONS} onChange={(v) => patchField("volc_tts_resource_id", v)} />
            </LabeledControl>
            <LabeledControl label="成本（分/万字符）" hint="用于计费估价，按火山控制台的实际单价选">
              <SettingsSelect value={form.volc_tts_fen_per_10k_chars} options={VOLC_PRICE_OPTIONS} onChange={(v) => patchField("volc_tts_fen_per_10k_chars", v)} />
            </LabeledControl>
          </div>
        </SettingsPanel>
      </div>
    </SettingsTabShell>
  );
}
