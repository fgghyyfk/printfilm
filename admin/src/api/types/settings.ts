/** 模型路由、运行时、存储、支付与站点设置的接口契约。 */
export type ModelCapabilityReadiness = {
  capability: string;
  label: string;
  model: string;
  ready: boolean;
  message: string;
};

export type AdminModelSettings = {
  openai_api_key: string;
  openai_base_url: string;
  model_llm: string;
  has_openai_api_key: boolean;
  ark_api_key: string;
  ark_base_url: string;
  model_image: string;
  model_image_45: string;
  model_video: string;
  model_audio: string;
  has_ark_api_key: boolean;
  ark_image_size: string;
  ark_video_resolution: string;
  ark_video_ratio: string;
  seedance_duration_min: number;
  seedance_duration_max: number;
  ark_video_poll_interval: number;
  ark_video_poll_timeout: number;
  pipeline_image_concurrency: number;
  pipeline_video_concurrency: number;
  pipeline_audio_concurrency: number;
  task_runtime_max_concurrency: number;
  task_user_max_concurrency: number;
  task_gpu_max_concurrency: number;
  task_gpu_user_max_concurrency: number;
  task_poll_max_concurrency: number;
  drama_user_video_job_limit: number;
  drama_fragment_max_attempts: number;
  ark_mock: boolean;
  avatar_base_url: string;
  avatar_api_key: string;
  has_avatar_api_key: boolean;
  avatar_engine: "infinitetalk" | "longcat";
  avatar_resolution: "480p" | "720p";
  avatar_poll_interval: number;
  avatar_poll_timeout: number;
  avatar_segment_max_sec: number;
  avatar_price_fen_per_sec: number;
  avatar_price_fen_per_sec_720p: number;
  avatar_ai_base_url: string;
  avatar_ai_api_key: string;
  has_avatar_ai_api_key: boolean;
  avatar_image_model: string;
  avatar_image_size: string;
  avatar_text_model: string;
  volc_tts_base_url: string;
  volc_tts_api_key: string;
  has_volc_tts_api_key: boolean;
  /** 生图改走 OpenAI（可选） */
  openai_image_enabled: boolean;
  openai_image_api_key: string;
  has_openai_image_api_key: boolean;
  openai_image_model: string;
  openai_image_quality: string;
  openai_image_scope: string;
  /** 生图渠道：openai 官方 | promptoll 自有网关 */
  openai_image_provider: string;
  promptoll_api_key: string;
  has_promptoll_api_key: boolean;
  volc_tts_resource_id: string;
  volc_tts_speech_rate: number;
  volc_tts_instruction: string;
  volc_tts_fen_per_10k_chars: number;
  oss_enabled: boolean;
  oss_endpoint: string;
  oss_region: string;
  oss_bucket: string;
  oss_folder: string;
  oss_access_key_id: string;
  oss_access_key_secret: string;
  oss_public_base: string;
  oss_upload_async: boolean;
  oss_upload_queue: string;
  has_oss_access_key_id: boolean;
  has_oss_access_key_secret: boolean;
  tos_endpoint: string;
  tos_bucket: string;
  tos_access_key: string;
  tos_secret_key: string;
  has_tos_access_key: boolean;
  has_tos_secret_key: boolean;
  cdn_base: string;
  epay_api_url: string;
  epay_pid: string;
  epay_key: string;
  epay_notify_url: string;
  epay_return_url: string;
  has_epay_key: boolean;
  payment_provider: "epay" | "stripe";
  stripe_secret_key: string;
  stripe_webhook_secret: string;
  stripe_currency: string;
  stripe_fx_rate: number;
  stripe_success_url: string;
  stripe_cancel_url: string;
  has_stripe_secret_key: boolean;
  has_stripe_webhook_secret: boolean;
  billing_enabled: boolean;
  billing_markup: number;
  billing_estimate_buffer: number;
  billing_seedance_video0: number;
  billing_seedance_video1: number;
  billing_llm_per_m: number;
  billing_seedream_per_m: number;
  billing_tts_per_m: number;
  billing_kie_fen_per_credit: number;
  billing_est_llm_tokens: number;
  billing_est_seedream_tokens: number;
  billing_est_tts_tokens: number;
  billing_est_seedance_tokens_per_sec: number;
  billing_signup_grant_fen: number;
  quota_enabled: boolean;
  new_user_quota: number;
  billing_user_alert_enabled: boolean;
  billing_user_alert_interval_fen: number;
  smtp_enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password: string;
  smtp_from: string;
  smtp_use_tls: boolean;
  has_smtp_password: boolean;
  public_base_url: string;
  ffmpeg_path: string;
  ffprobe_path: string;
  marketplace_enabled: boolean;
  /** 自定义广告法禁用词（逗号/顿号/空白分隔，命中即删除） */
  ad_banned_extra: string;
  /** 视觉理解模型 ID（电商套图看商品图） */
  model_vision: string;
  /** 电商套图规划单价（分） */
  ecom_set_plan_fen: number;
  source: string;
  updated_at?: string | null;
  readiness: ModelCapabilityReadiness[];
};

export type AdminRoutingChannel = {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  has_api_key: boolean;
  api_format: "openai";
  protocol: "auto" | "openai";
  models: string[];
  enabled: boolean;
  sort_order: number;
  clear_api_key?: boolean;
  advanced_config?: {
    provider: "tokenfree" | "byteplus";
    text_model: string;
    image_model: string;
    video_model: string;
    audio_model: string;
  } | null;
};

export type AdminLogicalModelBinding = {
  id: string;
  channel_id: string;
  upstream_model: string;
  enabled: boolean;
  priority: number;
  weight?: number | null;
};

export type AdminLogicalModel = {
  id: string;
  name: string;
  capability: "text" | "image" | "video" | "audio";
  strategy_id?: string | null;
  enabled: boolean;
  bindings: AdminLogicalModelBinding[];
};

export type AdminDefaultModels = {
  text_model: string;
  image_model: string;
  video_model: string;
  audio_model: string;
};

export type AdminRoutingSettings = {
  system_channels: AdminRoutingChannel[];
  logical_models: AdminLogicalModel[];
  default_models: AdminDefaultModels;
  media_reference_limits: Record<string, { images: number; videos: number; audios: number }>;
  validation_errors: string[];
  updated_at?: string | null;
};

/** 内置广告法词库的单个分类概览 */
export type AdWordCategory = {
  category: string;
  count: number;
  examples: string[];
};

/** 广告法词库概览：拦截档 / 提醒档 / 自定义词 */
export type AdWordsOverview = {
  block: AdWordCategory[];
  warn: AdWordCategory[];
  extra: string[];
};

/** 试检命中项 */
export type AdWordHit = {
  word: string;
  category: string;
  level: "block" | "warn";
};

/** 试检结果 */
export type AdWordsCheckResult = {
  clean: string;
  hits: AdWordHit[];
  messages: string[];
};
