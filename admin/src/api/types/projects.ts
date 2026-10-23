/** 用户、科普项目、漫剧资产与模板的管理端接口契约。 */
export type AdminProjectUsage = {
  charge_fen: number;
  cost_fen: number;
  tokens: number;
  calls: number;
  image_gens: number;
  video_gens: number;
  llm_calls: number;
  tts_gens?: number;
};

export type AdminTaskBrief = {
  id: number;
  domain: string;
  task_type: string;
  status: string;
  progress_percent: number;
  billing_charged_fen: number;
  billing_estimate_fen: number;
  error_message?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
};

export type AdminShotBrief = {
  id: number;
  shot_no: number;
  status: string;
  has_image: boolean;
  has_video: boolean;
  has_audio: boolean;
  duration: number;
};

export type AdminUserRow = {
  id: number;
  email: string;
  nickname: string;
  phone?: string;
  quota_left: number;
  balance_fen: number;
  frozen_fen: number;
  plan: string;
  role: string;
  created_at?: string | null;
};

export type AdminProject = {
  id: number;
  user_id: number;
  user_email?: string | null;
  template_id: string;
  title: string;
  status: string;
  progress: number;
  error_msg?: string | null;
  cover_url?: string | null;
  final_video_url?: string | null;
  pipeline_mode: string;
  created_at: string;
  updated_at: string;
  shot_count: number;
  charge_fen?: number;
  source_type?: string;
  source_text?: string;
  resolution_mode?: string;
  output_ratio?: string;
  voice_id?: string;
  usage?: AdminProjectUsage;
  shots?: AdminShotBrief[];
  recent_tasks?: AdminTaskBrief[];
};

export type AdminDramaProject = {
  id: number;
  user_id: number;
  user_email?: string | null;
  title: string;
  description?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  episode_count?: number;
  asset_count?: number;
  fragment_count?: number;
  charge_fen?: number;
  summary_status?: string | null;
  assets_seed_status?: string | null;
  episode_content_status?: string | null;
  usage?: AdminProjectUsage;
  episodes?: { id: number; name: string; fragment_count: number; fragment_plan_status?: string | null }[];
  assets?: {
    id: number;
    type: string;
    name?: string | null;
    has_cover: boolean;
    generation_status?: string | null;
  }[];
  recent_tasks?: AdminTaskBrief[];
};

export type AdminDramaAsset = {
  id: number;
  project_id: number;
  project_title?: string | null;
  user_id?: number | null;
  user_email?: string | null;
  type: string;
  asset_type: string;
  name?: string | null;
  cover?: string | null;
  url?: string | null;
  has_cover: boolean;
  generation_status?: string | null;
  derive_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  params?: Record<string, unknown> | null;
};

export type AdminDramaEpisode = {
  id: number;
  project_id: number;
  project_title?: string | null;
  user_id?: number | null;
  user_email?: string | null;
  name: string;
  fragment_count: number;
  fragment_plan_status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  fragments?: {
    id: number;
    sort_order: number;
    content: string;
    cover: string;
    video: string;
    duration_sec?: number | null;
    generation_status?: string | null;
    asset_ref_count: number;
  }[];
};

export type AdminDramaFragment = {
  id: number;
  episode_id: number;
  episode_name?: string | null;
  project_id: number;
  project_title?: string | null;
  user_id?: number | null;
  user_email?: string | null;
  sort_order: number;
  content: string;
  cover: string;
  video: string;
  duration_sec?: number | null;
  generation_status?: string | null;
  asset_ref_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  params?: Record<string, unknown> | null;
  asset_ids?: number[];
};

export type AdminWork = {
  id: number;
  project_id: number;
  user_id: number;
  user_email?: string | null;
  title: string;
  cover_url?: string | null;
  video_url: string;
  visibility: string;
  audit_status: string;
  published_at: string;
};

export type AdminTemplate = {
  id: string;
  name: string;
  description: string;
  category: string[];
  preview_cover: string;
  style_prefix: string;
  negative_prompt: string;
  default_ratio: string;
  shot_duration_min: number;
  shot_duration_max: number;
  llm_system_addon: string;
  seedream_config: Record<string, unknown>;
  seedance_config: Record<string, unknown>;
  audio_config: Record<string, unknown>;
  subtitle_config: Record<string, unknown>;
  sort_order: number;
  is_active: boolean;
  is_premium: boolean;
};
