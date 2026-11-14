/* 带货动态展示的纯函数：项目识别、静图镜头判定、镜头动作白名单（与后端 daihuo/motion.py 一致）。 */

/** 后端带货模板 id */
export const DAIHUO_TEMPLATE_ID = 'daihuo_product'

/** 镜头动作白名单 key，顺序即下拉顺序 */
export const MOTION_ACTION_KEYS = ['push_in', 'orbit', 'macro', 'light_sweep', 'pull_out', 'demo'] as const
export type MotionActionKey = (typeof MOTION_ACTION_KEYS)[number]

/** 动作短名（后端写进 shot.camera）→ key，用于回显当前动作 */
const ACTION_BY_LABEL: Record<string, MotionActionKey> = {
  缓慢推近: 'push_in',
  环绕展示: 'orbit',
  微距扫过: 'macro',
  光影扫过: 'light_sweep',
  拉远全景: 'pull_out',
  使用演示: 'demo',
}

/** 是否商品带货项目（套图或批量图片来源） */
export function isDaihuoProject(project: { template_id?: string | null; ecom_set_id?: number | null } | null | undefined): boolean {
  return Boolean(project && (project.ecom_set_id || project.template_id === DAIHUO_TEMPLATE_ID))
}

/** 镜头是否已确认不需要 AI 视频（带货静图推拉或真人隐私拦截） */
export function isVideoSkipped(shot: { video_skip_reason?: string | null }): boolean {
  return shot.video_skip_reason === 'static' || shot.video_skip_reason === 'privacy'
}

/** 带货镜头是否为静图：静图口播项目全部是静图，动态项目看标记 */
export function isStaticShot(pipelineMode: string | null | undefined, shot: { video_skip_reason?: string | null }): boolean {
  return pipelineMode !== 'full' || shot.video_skip_reason === 'static'
}

/** 由 camera 短名回显动作 key，未知时返回 null */
export function motionActionOf(camera: string | null | undefined): MotionActionKey | null {
  return ACTION_BY_LABEL[(camera || '').trim()] || null
}
