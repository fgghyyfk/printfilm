import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity } from "lucide-react";
import { toast } from "sonner";
import { api, type AdminModelSettings } from "@/api/client";
import {
  LabeledControl,
  SettingsLoading,
  SettingsPanel,
  SettingsStatusBar,
  SettingsTabShell,
} from "@/components/settings/SettingsPanel";
import { Switch } from "@/components/ui/switch";

const NUMBER_FIELDS = [
  "seedance_duration_min", "seedance_duration_max", "ark_video_poll_interval", "ark_video_poll_timeout",
  "pipeline_image_concurrency", "pipeline_video_concurrency", "pipeline_audio_concurrency",
  "task_runtime_max_concurrency", "task_user_max_concurrency", "task_gpu_max_concurrency", "task_gpu_user_max_concurrency", "task_poll_max_concurrency",
  "drama_user_video_job_limit", "drama_fragment_max_attempts",
] as const;
type NumberField = (typeof NUMBER_FIELDS)[number];

// 运行参数配置（并发、质量、Mock 等 flat 字段）
export function RuntimeSettingsPanel() {
  const [form, setForm] = useState<AdminModelSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [numberDrafts, setNumberDrafts] = useState<Partial<Record<NumberField, string>>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<AdminModelSettings>("/api/admin/settings/models");
      setForm(data);
      setNumberDrafts({});
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const statusItems = useMemo(
    () =>
      (form?.readiness ?? []).map((item) => ({
        id: item.capability,
        label: item.label,
        ready: item.ready,
        readyText: item.model || "就绪",
        pendingText: "未就绪",
      })),
    [form?.readiness],
  );

  function patchField<K extends keyof AdminModelSettings>(key: K, value: AdminModelSettings[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  const errors: Partial<Record<NumberField | "ark_image_size" | "ark_video_resolution" | "ark_video_ratio", string>> = {};
  if (form) {
    NUMBER_FIELDS.forEach((key) => {
      const raw = numberDrafts[key] ?? String(form[key]);
      const value = Number(raw);
      const seconds = key === "ark_video_poll_interval" || key === "ark_video_poll_timeout";
      if (!raw.trim() || !Number.isFinite(value) || value <= 0) errors[key] = seconds ? "请输入大于 0 的秒数" : "请输入不小于 1 的整数";
      else if (!seconds && !Number.isInteger(value)) errors[key] = "请输入整数";
    });
    if (!errors.seedance_duration_min && !errors.seedance_duration_max && form.seedance_duration_min > form.seedance_duration_max) {
      errors.seedance_duration_max = "最大时长不能小于最小时长";
    }
    if (!errors.ark_video_poll_interval && !errors.ark_video_poll_timeout && form.ark_video_poll_timeout <= form.ark_video_poll_interval) {
      errors.ark_video_poll_timeout = "超时应大于轮询间隔";
    }
    (["ark_image_size", "ark_video_resolution", "ark_video_ratio"] as const).forEach((key) => {
      if (!form[key].trim()) errors[key] = "请填写默认值";
    });
  }
  const hasErrors = Object.keys(errors).length > 0;

  function numberField(key: NumberField, label: string, hint: string, unit: string) {
    if (!form) return null;
    const seconds = key === "ark_video_poll_interval" || key === "ark_video_poll_timeout";
    return (
      <LabeledControl label={label} hint={hint} error={errors[key]} required>
        <div className="relative">
          <input
            className="settings-input !pr-12"
            type="number" required min={seconds ? 0 : 1} step={seconds ? "any" : 1}
            aria-invalid={Boolean(errors[key])}
            value={numberDrafts[key] ?? form[key]}
            onChange={(event) => {
              const raw = event.target.value;
              setNumberDrafts((prev) => ({ ...prev, [key]: raw }));
              if (raw.trim() && Number.isFinite(Number(raw))) patchField(key, Number(raw));
            }}
          />
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[var(--admin-muted)]">{unit}</span>
        </div>
      </LabeledControl>
    );
  }

  async function handleSave() {
    if (!form || saving || hasErrors) return;
    setSaving(true);
    try {
      const body = {
        ark_image_size: form.ark_image_size,
        ark_video_resolution: form.ark_video_resolution,
        ark_video_ratio: form.ark_video_ratio,
        seedance_duration_min: form.seedance_duration_min,
        seedance_duration_max: form.seedance_duration_max,
        ark_video_poll_interval: form.ark_video_poll_interval,
        ark_video_poll_timeout: form.ark_video_poll_timeout,
        pipeline_image_concurrency: form.pipeline_image_concurrency,
        pipeline_video_concurrency: form.pipeline_video_concurrency,
        pipeline_audio_concurrency: form.pipeline_audio_concurrency,
        task_runtime_max_concurrency: form.task_runtime_max_concurrency,
        task_user_max_concurrency: form.task_user_max_concurrency,
        task_gpu_max_concurrency: form.task_gpu_max_concurrency,
        task_gpu_user_max_concurrency: form.task_gpu_user_max_concurrency,
        task_poll_max_concurrency: form.task_poll_max_concurrency,
        drama_user_video_job_limit: form.drama_user_video_job_limit,
        drama_fragment_max_attempts: form.drama_fragment_max_attempts,
        ark_mock: form.ark_mock,
      };
      await api("/api/admin/settings/models", { method: "PATCH", body: JSON.stringify(body) });
      await load();
      toast.success("运行参数已保存");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !form) {
    return <SettingsLoading />;
  }

  return (
    <SettingsTabShell onSave={handleSave} saving={saving} disabled={hasErrors} saveLabel="保存运行设置">
      <SettingsStatusBar
        title="路由就绪状态"
        items={
          statusItems.length > 0
            ? statusItems
            : [{ id: "empty", label: "能力路由", ready: false, pendingText: "请先在「模型」填写 Key 并选择模型" }]
        }
        extra={
          <span className="settings-status-extra">
            {form.readiness?.length && form.readiness.every((item) => item.ready) ? "四类能力已就绪" : "请在「模型」填写 TokenFree Key 并选择模型"}
          </span>
        }
      />

      <SettingsPanel title="生成默认值" description="设置新生成任务的默认画质、画幅和时长；具体支持范围仍由所选模型决定。">
        <div className="settings-field-grid settings-field-grid--3">
          <LabeledControl label="生图尺寸" hint="沿用上游支持的尺寸，例如 2K 或 1024x1024。" error={errors.ark_image_size} required>
            <input className="settings-input" required placeholder="例如 2K" aria-invalid={Boolean(errors.ark_image_size)} value={form.ark_image_size} onChange={(e) => patchField("ark_image_size", e.target.value)} />
          </LabeledControl>
          <LabeledControl label="视频清晰度" hint="例如 480p、720p；以当前模型支持值为准。" error={errors.ark_video_resolution} required>
            <input className="settings-input" required placeholder="例如 720p" aria-invalid={Boolean(errors.ark_video_resolution)} value={form.ark_video_resolution} onChange={(e) => patchField("ark_video_resolution", e.target.value)} />
          </LabeledControl>
          <LabeledControl label="视频画幅" hint="宽:高，例如 16:9 或 9:16。" error={errors.ark_video_ratio} required>
            <input className="settings-input" required placeholder="例如 16:9" aria-invalid={Boolean(errors.ark_video_ratio)} value={form.ark_video_ratio} onChange={(e) => patchField("ark_video_ratio", e.target.value)} />
          </LabeledControl>
        </div>
        <div className="settings-field-grid settings-field-grid--2 mt-5">
          {numberField("seedance_duration_min", "Seedance 最小时长", "不小于 1 秒，与最大时长一起限制单段生成时长。", "秒")}
          {numberField("seedance_duration_max", "Seedance 最大时长", "不小于最小时长；最终仍受模型时长范围限制。", "秒")}
        </div>
      </SettingsPanel>

      <SettingsPanel title="任务并发" description="控制同时执行的任务数量，以及单个用户可占用的资源。">
        <div className="settings-field-grid settings-field-grid--3">
          {numberField("task_runtime_max_concurrency", "全站执行槽位", "全站可同时运行的 Worker 任务数，不小于 1。", "个")}
          {numberField("task_user_max_concurrency", "单用户执行槽位", "单个用户可占用的任务数，仍受全站槽位约束。", "个")}
          {numberField("task_gpu_max_concurrency", "数字人 GPU 槽位", "数字人对口型等长任务单独排队，不占上面的执行槽位。", "个")}
          {numberField("task_gpu_user_max_concurrency", "单用户 GPU 槽位", "单个用户同时进行的数字人长任务数；设 1 时各用户轮流进 GPU。", "个")}
          {numberField("drama_user_video_job_limit", "单用户漫剧视频在途上限", "限制排队与处理中的视频任务总数，不小于 1。", "个")}
        </div>
        <div className="settings-runtime-note mt-5">
          <Activity className="h-4 w-4 shrink-0 text-[var(--admin-forest)]" />
          <span>当前草稿：全站 <strong>{form.task_runtime_max_concurrency}</strong> 个执行槽位，单用户最多 <strong>{form.task_user_max_concurrency}</strong> 个；等待上游轮询的任务不占 Worker 槽位。</span>
        </div>
      </SettingsPanel>

      <details className="settings-advanced">
        <summary><span>高级运行参数</span><span>管线分配、轮询与模拟模式</span></summary>
        <SettingsPanel title="生成管线" description="分别控制各类媒体任务的并行处理数；均为不小于 1 的整数。">
          <div className="settings-field-grid settings-field-grid--3">
            {numberField("pipeline_image_concurrency", "生图并发", "同时处理的图像任务数。", "路")}
            {numberField("pipeline_video_concurrency", "视频并发", "同时处理的视频任务数。", "路")}
            {numberField("pipeline_audio_concurrency", "配音并发", "同时处理的配音任务数。", "路")}
          </div>
        </SettingsPanel>
        <SettingsPanel title="上游轮询与重试" description="在查询频率和等待时长之间取得平衡；超时应大于查询间隔。">
          <div className="settings-field-grid settings-field-grid--2">
            {numberField("ark_video_poll_interval", "视频轮询间隔", "大于 0 秒；间隔越短，上游查询频率越高。", "秒")}
            {numberField("ark_video_poll_timeout", "视频轮询超时", "大于轮询间隔；超过此时长后结束等待。", "秒")}
            {numberField("task_poll_max_concurrency", "每轮上游查询并发", "Selector 每轮非阻塞查询的最大路数，不小于 1。", "路")}
            {numberField("drama_fragment_max_attempts", "分镜视频最大尝试次数", "包含首次生成与失败后的重试，不小于 1。", "次")}
          </div>
        </SettingsPanel>
        <SettingsPanel title="开发与调试" description="切换生成请求的运行方式。">
          <div className="settings-toggle-row">
            <div><strong>ARK Mock 模式</strong><span>使用模拟结果调试 ARK 接口，开启后不调用对应真实上游。</span></div>
            <Switch aria-label="ARK Mock 模式" checked={form.ark_mock} onCheckedChange={(v) => patchField("ark_mock", v)} />
          </div>
        </SettingsPanel>
      </details>
    </SettingsTabShell>
  );
}
