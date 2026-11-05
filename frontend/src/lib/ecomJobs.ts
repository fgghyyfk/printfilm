/** 验证整个上传批次，返回可翻译的错误键；限制与后端保持一致。 */
export function validateEcomImages(files: readonly Pick<File, 'type' | 'size'>[], maxImages: number): 'required' | 'count' | 'type' | 'size' | null {
  if (!files.length) return 'required'
  if (files.length > maxImages) return 'count'
  if (files.some((file) => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) return 'type'
  if (files.some((file) => file.size > 10 * 1024 * 1024)) return 'size'
  return null
}

/** 任务处于排队或执行中时继续轮询。 */
export function ecomJobNeedsPolling(job: { status: string } | null): boolean {
  return job?.status === 'queued' || job?.status === 'running'
}

/* 公共诊断短句与界面翻译键的对应关系，保留未知诊断便于排障。 */
const ERROR_KEYS: Record<string, string> = {
  '参数错误，请检查图片数量和选项': 'invalid',
  '输入参数有误，请检查后重试': 'invalid',
  '参数错误': 'invalid',
  '任务已取消': 'cancelled',
  '生成失败，请重试': 'generation',
  '请求的内容不存在': 'notFound',
  '任务不存在': 'notFound',
  '图片不存在': 'notFound',
  '商品图仅支持 png / jpg / webp': 'type',
  '请上传商品图': 'required',
  '商品图不能超过 10MB': 'size',
  '图片无法识别，请换一张': 'image',
  '这张图正在生成中': 'busy',
  '还没有生成好的图片': 'empty',
  '原任务尚未结束，请稍后查看': 'busy',
  '只能重试失败的图片': 'retryFailed',
  '生成服务暂时不可用，请稍后再试': 'unavailable',
  '生成服务未能完成请求，请查看任务记录或联系客服': 'unavailable',
  '生成失败，请稍后查看任务状态或联系客服': 'generation',
  '上游响应超时，请先查看任务状态，避免重复提交': 'timeout',
  '发送请求超时，结果尚未确认，请先查看任务状态': 'timeout',
  '暂时无法连接生成服务，请稍后查看任务状态': 'unavailable',
  '平台模型账户暂不可用，请联系管理员处理': 'account',
  '模型服务认证暂不可用，请联系管理员处理': 'account',
  '当前模型渠道未开通，请更换模型或联系管理员': 'account',
  '本次调用的结果和费用待核对，请勿重复生成，请联系客服': 'billingReview',
}

/** 翻译已知服务端诊断，保持原始错误字符串不变以供计费分类与留痕。 */
export function ecomJobErrorText(message: string, t: (key: string) => string): string {
  const key = ERROR_KEYS[message]
  return key ? t(`ecomJobs.serverErrors.${key}`) : message
}

/** 返回风格复刻各模式的产品数量上限。 */
export function styleProductLimit(mode: 'single' | 'batch'): number {
  return mode === 'single' ? 6 : 20
}

/** 以实际输出张数计算批次估价，参考设计图不计作输出。 */
export function styleOutputCount(mode: 'single' | 'batch', products: number, count: number): number {
  return mode === 'single' ? count : products
}
