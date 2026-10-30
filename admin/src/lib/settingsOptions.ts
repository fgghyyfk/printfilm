import type { SelectOption } from "@/components/settings/SettingsSelect";

/** 生成数字选项：values 每个值配同一个单位后缀，labels 可覆盖个别文字。 */
export function numberOptions(values: number[], unit = "", labels: Record<number, string> = {}): SelectOption<number>[] {
  return values.map((v) => ({ value: v, label: labels[v] ?? `${v}${unit}` }));
}

/* 系统设置各下拉项的固定选项（不让运营手填） */

// 生成默认参数
export const IMAGE_SIZE_OPTIONS: SelectOption<string>[] = [
  { value: "1k", label: "1K" },
  { value: "2k", label: "2K（推荐）" },
  { value: "3k", label: "3K" },
  { value: "4k", label: "4K" },
];
export const VIDEO_RESOLUTION_OPTIONS: SelectOption<string>[] = [
  { value: "480p", label: "480p" },
  { value: "720p", label: "720p" },
  { value: "1080p", label: "1080p" },
];
export const VIDEO_RATIO_OPTIONS: SelectOption<string>[] = [
  { value: "16:9", label: "16:9 横屏" },
  { value: "9:16", label: "9:16 竖屏" },
  { value: "1:1", label: "1:1 方形" },
  { value: "4:3", label: "4:3" },
  { value: "3:4", label: "3:4" },
  { value: "21:9", label: "21:9 宽银幕" },
];

// 运行参数（数字）
export const RUNTIME_NUMBER_OPTIONS: Record<string, SelectOption<number>[]> = {
  seedance_duration_min: numberOptions([2, 3, 4, 5, 6, 8, 10], " 秒"),
  seedance_duration_max: numberOptions([5, 8, 10, 12, 15, 20, 30], " 秒"),
  task_runtime_max_concurrency: numberOptions([1, 2, 4, 6, 8, 12, 16, 24, 32], " 个"),
  task_user_max_concurrency: numberOptions([1, 2, 3, 4, 6, 8], " 个"),
  task_gpu_max_concurrency: numberOptions([1, 2, 3, 4, 6, 8], " 个"),
  task_gpu_user_max_concurrency: numberOptions([1, 2, 3, 4], " 个"),
  drama_user_video_job_limit: numberOptions([4, 6, 8, 12, 16, 24, 32], " 个"),
  pipeline_image_concurrency: numberOptions([1, 2, 3, 4, 6, 8], " 路"),
  pipeline_video_concurrency: numberOptions([1, 2, 4, 6, 8, 10, 12, 16], " 路"),
  pipeline_audio_concurrency: numberOptions([1, 2, 3, 4, 6, 8], " 路"),
  ark_video_poll_interval: numberOptions([3, 5, 8, 10, 15, 30], " 秒"),
  ark_video_poll_timeout: numberOptions([300, 600, 900, 1200, 1800, 3600], "", {
    300: "5 分钟", 600: "10 分钟", 900: "15 分钟", 1200: "20 分钟", 1800: "30 分钟", 3600: "1 小时",
  }),
  task_poll_max_concurrency: numberOptions([5, 10, 20, 30, 50], " 路"),
  drama_fragment_max_attempts: numberOptions([1, 2, 3, 4, 5], " 次"),
};

// 数字人
export const AVATAR_SEGMENT_OPTIONS = numberOptions([10, 12, 15, 20, 25, 30], " 秒");
export const AVATAR_POLL_INTERVAL_OPTIONS = numberOptions([5, 10, 15, 30, 60], " 秒");
export const AVATAR_POLL_TIMEOUT_OPTIONS = numberOptions([3600, 7200, 14400, 21600, 28800], "", {
  3600: "1 小时", 7200: "2 小时", 14400: "4 小时", 21600: "6 小时", 28800: "8 小时",
});
export const AVATAR_PRICE_OPTIONS = numberOptions([0, 1, 2, 3, 5, 8, 10, 15, 20], " 分/秒", { 0: "0（不收费）" });
export const AVATAR_AI_BASE_OPTIONS: SelectOption<string>[] = [
  { value: "https://promptoll.com/v1", label: "promptoll（https://promptoll.com/v1）" },
];
export const AVATAR_IMAGE_MODEL_OPTIONS: SelectOption<string>[] = [
  { value: "gemini-3.1-flash-image", label: "Gemini Flash Image（卡通化保脸最稳）" },
  { value: "gpt-image-2", label: "GPT Image 2（画质最好，真人推荐）" },
  { value: "gemini-2.5-flash-image", label: "Gemini 2.5 Flash Image（便宜）" },
];
export const AVATAR_IMAGE_SIZE_OPTIONS: SelectOption<string>[] = [
  { value: "1024x1536", label: "1024×1536 竖屏（推荐）" },
  { value: "1024x1024", label: "1024×1024 方形" },
  { value: "1536x1024", label: "1536×1024 横屏" },
];
// promptoll 上已开通的文本模型（写分段口播与场景）
export const AVATAR_TEXT_MODEL_OPTIONS: SelectOption<string>[] = [
  { value: "qwen3.6-plus", label: "通义 qwen3.6-plus（默认）" },
  { value: "qwen3.7-plus", label: "通义 qwen3.7-plus" },
  { value: "qwen3.8-max", label: "通义 qwen3.8-max（更强）" },
  { value: "kimi-k2.6", label: "Kimi K2.6" },
  { value: "kimi-k3", label: "Kimi K3" },
  { value: "deepseek-v4-pro", label: "DeepSeek V4 Pro" },
  { value: "doubao-seed-2.1-pro", label: "豆包 Seed 2.1 Pro" },
  { value: "glm-5.3", label: "智谱 GLM-5.3" },
  { value: "MiniMax-M2.7", label: "MiniMax M2.7" },
  { value: "gpt-5.5", label: "GPT-5.5" },
  { value: "claude-sonnet-5", label: "Claude Sonnet 5" },
];

// 火山引擎豆包配音
export const VOLC_SPEECH_RATE_OPTIONS = numberOptions([-20, -10, 0, 10, 20, 30, 50], "", {
  [-20]: "-20（慢）", [-10]: "-10（稍慢）", 0: "0（正常）", 10: "10（稍快，约 1.1 倍）", 20: "20（快）", 30: "30（很快）", 50: "50（1.5 倍）",
});
export const VOLC_INSTRUCTION_OPTIONS: SelectOption<string>[] = [
  { value: "用热情、有感染力的短视频口播博主语气，自然口语，有情绪起伏", label: "热情口播（推荐）" },
  { value: "用亲切、耐心的语气讲解，像朋友聊天一样自然", label: "亲切讲解" },
  { value: "用沉稳、专业、可信的语气讲述，语速适中", label: "沉稳专业" },
  { value: "用轻松幽默、带点调侃的语气说，节奏明快", label: "轻松幽默" },
  { value: "用温柔、治愈的语气慢慢说", label: "温柔治愈" },
  { value: "", label: "不加语气指令" },
];
export const VOLC_RESOURCE_OPTIONS: SelectOption<string>[] = [
  { value: "seed-tts-1.0", label: "seed-tts-1.0（按字符计费）" },
  { value: "seed-tts-1.0-concurr", label: "seed-tts-1.0-concurr（按并发计费）" },
];
export const VOLC_PRICE_OPTIONS = numberOptions([100, 200, 300, 450, 500, 800, 1000], " 分/万字符");

// 存储 OSS：阿里云地域（Endpoint 由地域推出）
export const OSS_REGIONS: { region: string; label: string }[] = [
  { region: "cn-hangzhou", label: "华东1（杭州）" },
  { region: "cn-shanghai", label: "华东2（上海）" },
  { region: "cn-nanjing", label: "华东5（南京）" },
  { region: "cn-qingdao", label: "华北1（青岛）" },
  { region: "cn-beijing", label: "华北2（北京）" },
  { region: "cn-zhangjiakou", label: "华北3（张家口）" },
  { region: "cn-huhehaote", label: "华北5（呼和浩特）" },
  { region: "cn-wulanchabu", label: "华北6（乌兰察布）" },
  { region: "cn-shenzhen", label: "华南1（深圳）" },
  { region: "cn-heyuan", label: "华南2（河源）" },
  { region: "cn-guangzhou", label: "华南3（广州）" },
  { region: "cn-chengdu", label: "西南1（成都）" },
  { region: "cn-hongkong", label: "中国香港" },
  { region: "ap-southeast-1", label: "新加坡" },
  { region: "ap-northeast-1", label: "日本（东京）" },
  { region: "us-west-1", label: "美国（硅谷）" },
  { region: "us-east-1", label: "美国（弗吉尼亚）" },
  { region: "eu-central-1", label: "德国（法兰克福）" },
];
export const OSS_REGION_OPTIONS: SelectOption<string>[] = OSS_REGIONS.map((r) => ({
  value: r.region,
  label: `${r.label} · ${r.region}`,
}));
export const OSS_ENDPOINT_OPTIONS: SelectOption<string>[] = OSS_REGIONS.flatMap((r) => [
  { value: `oss-${r.region}.aliyuncs.com`, label: `${r.label} · oss-${r.region}.aliyuncs.com` },
  { value: `oss-${r.region}-internal.aliyuncs.com`, label: `${r.label} 内网 · oss-${r.region}-internal.aliyuncs.com` },
]);
export const OSS_QUEUE_OPTIONS: SelectOption<string>[] = [{ value: "oss", label: "oss（默认）" }];

// 支付计费
export const SIGNUP_GRANT_OPTIONS = numberOptions([0, 100, 200, 500, 1000, 2000, 5000], " 分", {
  0: "0（不赠送）", 100: "100 分（¥1）", 200: "200 分（¥2）", 500: "500 分（¥5）", 1000: "1000 分（¥10）",
  2000: "2000 分（¥20）", 5000: "5000 分（¥50）",
});
export const ALERT_INTERVAL_OPTIONS = numberOptions([1000, 2000, 5000, 10000, 20000, 50000], " 分", {
  1000: "每 ¥10", 2000: "每 ¥20", 5000: "每 ¥50", 10000: "每 ¥100", 20000: "每 ¥200", 50000: "每 ¥500",
});
export const MULTIPLIER_OPTIONS = numberOptions([1, 1.1, 1.2, 1.3, 1.5, 1.8, 2, 3], " 倍");
export const ESTIMATE_TOKEN_OPTIONS: Record<string, SelectOption<number>[]> = {
  billing_est_llm_tokens: numberOptions([20000, 40000, 80000, 120000, 200000], " Token/次", { 80000: "80000 Token/次（默认）" }),
  billing_est_seedream_tokens: numberOptions([20000, 30000, 45000, 60000, 90000], " Token/次", { 45000: "45000 Token/次（默认）" }),
  billing_est_tts_tokens: numberOptions([2000, 5000, 10000, 20000], " Token/次", { 5000: "5000 Token/次（默认）" }),
  billing_est_seedance_tokens_per_sec: numberOptions([16000, 24000, 32000, 48000, 64000], " Token/秒", { 32000: "32000 Token/秒（默认）" }),
};
export const SMTP_HOST_OPTIONS: SelectOption<string>[] = [
  { value: "smtp.exmail.qq.com", label: "腾讯企业邮 · smtp.exmail.qq.com" },
  { value: "smtp.qq.com", label: "QQ 邮箱 · smtp.qq.com" },
  { value: "smtp.qiye.aliyun.com", label: "阿里企业邮 · smtp.qiye.aliyun.com" },
  { value: "smtpdm.aliyun.com", label: "阿里云邮件推送 · smtpdm.aliyun.com" },
  { value: "smtp.163.com", label: "网易 163 · smtp.163.com" },
  { value: "smtp.qiye.163.com", label: "网易企业邮 · smtp.qiye.163.com" },
  { value: "smtp.feishu.cn", label: "飞书邮箱 · smtp.feishu.cn" },
  { value: "smtp.gmail.com", label: "Gmail · smtp.gmail.com" },
  { value: "smtp.office365.com", label: "Outlook / Microsoft 365 · smtp.office365.com" },
];
export const SMTP_PORT_OPTIONS = numberOptions([25, 465, 587, 2525], "", {
  25: "25（明文，不推荐）", 465: "465（SSL）", 587: "587（STARTTLS，推荐）", 2525: "2525（备用）",
});

// 站点工具
export const FFMPEG_OPTIONS: SelectOption<string>[] = [
  { value: "ffmpeg", label: "ffmpeg（系统 PATH）" },
  { value: "/usr/bin/ffmpeg", label: "/usr/bin/ffmpeg" },
  { value: "/usr/local/bin/ffmpeg", label: "/usr/local/bin/ffmpeg" },
  { value: "/opt/homebrew/bin/ffmpeg", label: "/opt/homebrew/bin/ffmpeg（Mac）" },
];
export const FFPROBE_OPTIONS: SelectOption<string>[] = FFMPEG_OPTIONS.map((o) => ({
  value: o.value.replace(/ffmpeg$/, "ffprobe"),
  label: o.label.replace(/ffmpeg/g, "ffprobe"),
}));
