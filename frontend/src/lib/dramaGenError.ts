/** 漫剧生成队列：把上游/平台原始错误翻成可读中文，并附处理建议 */

import { dialog } from './dialog'
import { isBillingError } from './billingError'

export type DramaGenErrorView = {
  /** 短标题 */
  title: string
  /** 用户可读说明 */
  message: string
  /** 建议操作 */
  suggestion?: string
  /** 是否余额不足（展示充值跳转） */
  billingBlocked?: boolean
  /** 是否上游模型账户欠费（提醒管理员，非用户钱包） */
  upstreamAccountBlocked?: boolean
}

/** 是否为上游 Seedream 账户欠费 */
export function isUpstreamAccountError(message: string): boolean {
  return /AccountOverdueError|上游 Seedream 账户欠费|上游.*账户欠费|平台模型账户暂不可用|Credits insufficient|积分不足|余额不足.*[Kk]ie|Kie.*积分/i.test(message)
}

// 从 Seedance JSON 文案里取出 content[n]
function extractContentIndex(raw: string): number | null {
  const m = raw.match(/content\[(\d+)\]/i)
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) ? n : null
}

/** 判断文案是否像「具体根因」（优先于「重试上限」等包装句） */
function looksLikeRootCause(text: string): boolean {
  return /PrivacyInformation|InputImageSensitive|SensitiveContentDetected|参考图疑似|参考音频过短|参考音频过长|无法读取参考音频时长|may contain real person|Seedance create error|上一镜失败|无法衔接|分镜已变更|分镜上下文|InputTextSensitive|resource download failed|audio_url|audio duration|Credits insufficient|File type not supported|参考图格式不支持|invalid image format|参考图格式无效|未开通/i.test(
    text,
  )
}

/**
 * 从多条候选错误里挑出最具体的根因（例如隐私图审核），
 * 避免只展示「重试超过上限」这类包装文案。
 */
export function pickRootDramaGenError(
  candidates: Array<string | null | undefined>,
): string {
  const cleaned = candidates.map((c) => String(c || '').trim()).filter(Boolean)
  const root = cleaned.find(looksLikeRootCause)
  if (root) return root
  return cleaned[0] || ''
}

/**
 * 将任务 error / error_message 转为前端展示文案。
 * 只重建已知类别，不回显历史队列或上游原始诊断。
 */
export function formatDramaGenError(raw: string | null | undefined, code?: string): DramaGenErrorView {
  const text = String(raw || '').trim()
  if (code === 'billing_review_pending' || /费用.*待核对/.test(text)) {
    return {
      title: '费用待核对',
      message: '本次调用的结果和费用待核对，请勿重复生成。',
      suggestion: '请保留任务记录并联系客服核对。',
    }
  }
  if (code === 'recovery_limit' || /任务执行中断已达 \d+ 次/.test(text)) {
    const count = text.match(/已达 (\d{1,6}) 次/)
    return {
      title: '任务已停止自动恢复',
      message: count ? `任务执行中断已达 ${Number(count[1])} 次，已停止自动恢复。` : '任务多次执行中断，已停止自动恢复。',
      suggestion: '请查看任务记录并联系管理员排查。',
    }
  }
  if (!text) {
    return {
      title: '生成失败',
      message: '任务未能完成，且未记录具体错误信息。',
      suggestion: '请稍后重试；若反复失败，检查网络/代理是否能访问 TokenFree，以及后台模型渠道密钥。',
    }
  }

  if (code === 'upstream_timeout' || code === 'upstream_submit_unknown' || /ReadTimeout|WriteTimeout|等待上游超时|响应超时/i.test(text)) {
    return {
      title: '上游响应超时',
      message: '上游响应超时，结果尚未确认，请先查看原任务状态。',
      suggestion:
        '请等待或查看任务记录，避免重复提交；持续无法确认时请联系客服。',
    }
  }

  if (code === 'upstream_unavailable' || /网络错误|ConnectError|ConnectTimeout|无法连接上游|无法连接生成服务|tokenfree\.com/i.test(text)) {
    return {
      title: '无法连接图片/视频服务',
      message: '暂时无法连接生成服务。',
      suggestion:
        '本机当前连不上上游（常见于代理未放行或网络中断）。请检查网络/代理后重试，并确认后台 TokenFree 渠道密钥有效。',
    }
  }

  if (/^生图失败$/.test(text)) {
    return {
      title: '生图失败',
      message: '生图未成功，但旧任务未保存具体原因（多为上游连接失败且错误文案为空）。',
      suggestion:
        '请重新生成一次；新版本会写出明确错误。仍失败时检查 TokenFree 网络与密钥。',
    }
  }

  if (code === 'upstream_account_unavailable' || isUpstreamAccountError(text) || (/Seedream error 403/i.test(text) && /AccountOverdue/i.test(text))) {
    return {
      title: '平台上游账户欠费',
      message:
        '上游 Seedream 模型账户余额不足，生图请求被拒绝。这是站点上游模型账户欠费，不是您个人钱包余额问题。',
      suggestion: '请联系站点管理员在 TokenFree 控制台充值；充值完成后请重试生图。',
      upstreamAccountBlocked: true,
    }
  }

  if (code === 'upstream_auth_unavailable') {
    return {
      title: '模型服务认证不可用',
      message: '模型服务认证暂不可用。',
      suggestion: '请联系管理员检查模型渠道配置。',
    }
  }

  if (isBillingError(text)) {
    return {
      title: '余额不足',
      message: '当前钱包余额不足，无法继续生成。',
      suggestion: '请先充值后再重试该任务。',
      billingBlocked: true,
    }
  }

  if (
    code === 'reference_image_privacy' || /参考图疑似真人|PrivacyInformation|InputImageSensitive|SensitiveContentDetected|may contain real person/i.test(
      text,
    )
  ) {
    const idx = extractContentIndex(text)
    const where =
      idx != null
        ? `（提交内容第 ${idx + 1} 项 / content[${idx}]，多为角色或场景参考图）`
        : '（某张参考图）'
    return {
      title: '参考图疑似真人',
      message: `视频服务审核未通过：输入图片${where}可能含真人肖像，已拒绝生成。`,
      suggestion:
        '打开左侧资产，为相关角色/场景重新用 AI 生成偏动漫或插画的形象（避免真人照片），或上传合规图后再重新生成该分镜。',
    }
  }

  if (/重试超过上限|超过重试上限|内部自动重试超过上限/.test(text)) {
    return {
      title: '多次生成仍失败',
      message: '本次任务的自动重试已耗尽，请检查参考素材和文案。',
      suggestion:
        '这是同一次任务内的自动重试耗尽，不是禁止你再点生成。请根据真实原因（常见是参考图真人审核）改素材或文案后，再重新点生成。',
    }
  }

  if (/上一镜失败|无法衔接尾帧/.test(text)) {
    return {
      title: '无法衔接上一镜',
      message: '本镜依赖上一镜的尾帧衔接，但上一镜未成功，因此本镜未开始生成。',
      suggestion: '先修复并重新生成失败的上一镜，再按镜序生成后续片段。',
    }
  }

  if (code === 'fragment_changed' || /分镜已变更|分镜已更新|分镜上下文丢失|分镜不存在/.test(text)) {
    return {
      title: '分镜已更新',
      message: '分镜在生成过程中被保存或重切，旧任务已失效。',
      suggestion: '请回到分集页，用当前分镜列表重新点生成；不要重试旧任务。',
    }
  }

  if (code === 'text_moderation' || /分镜文案未通过内容审核/.test(text) || (/InputTextSensitive|text.*sensitive|敏感/i.test(text) && /Seedance|create error/i.test(text))) {
    return {
      title: '文案未通过审核',
      message: '分镜脚本或提示词触发了内容安全审核。',
      suggestion: '请修改分镜中的敏感表述后重试。',
    }
  }

  if (/resource download failed|audio_url/i.test(text) && !/audio duration/i.test(text)) {
    return {
      title: '参考音频无法下载',
      message: '音色参考文件地址无效或暂时无法访问。',
      suggestion: '检查角色绑定的试听音频，重新生成或更换音色后再试。',
    }
  }

  if (code === 'reference_audio_unavailable' || /无法读取参考音频时长|参考音频地址为空|参考音频无法读取/i.test(text)) {
    return {
      title: '参考音频无法校验',
      message: '无法读取参考音频或地址无效。',
      suggestion: '请检查角色/旁白试听文件是否可访问，重新上传或生成后再生成该分镜。',
    }
  }

  if (/参考音频过长/i.test(text)) {
    return {
      title: '参考音频过长',
      message: '参考音频过长，须截短至 30 秒以内。',
      suggestion: '请截短对应角色/旁白试听音频后再生成该分镜。这不是参考图问题。',
    }
  }

  // Seedance r2v：reference_audio 须 ≥ 1.8 秒（不是参考图）
  if (
    /参考音频过短/i.test(text) ||
    (/audio duration|1\.8/i.test(text) && /audio|音色|reference_audio|content\[/i.test(text))
  ) {
    const idx = extractContentIndex(text)
    const where =
      (idx != null ? `提交内容第 ${idx + 1} 项 / content[${idx}]（参考音频，不是图片）` : '某条角色/旁白音色')
    return {
      title: '参考音频过短',
      message: `视频服务要求参考音频时长 ≥ 1.8 秒，当前过短：${where}。`,
      suggestion:
        '打开左侧对应角色或旁白资产，重新生成/上传更长的试听音频（建议 ≥ 2 秒）后再生成该分镜。这不是参考图问题。',
    }
  }

  if (/only support adaptive aspect ratio|adaptive aspect ratio/i.test(text)) {
    return {
      title: '画幅参数不兼容',
      message: '当前视频通道的图生视频若走单首帧，固定比例可能被拒绝。',
      suggestion: '请重新生成该分镜；服务端会按参考图自适应画幅。',
    }
  }

  if (/未开通|无可用渠道|distributor/i.test(text)) {
    return {
      title: '图像模型未开通',
      message: '当前模型渠道未开通。',
      suggestion: '请管理员在 TokenFree 控制台为 default 分组开通该模型（如 GPT Image 2.5）后再生成。',
    }
  }

  if (/File type not supported|参考图格式不支持|不支持 SVG/i.test(text)) {
    return {
      title: '参考图格式不支持',
      message: '参考图格式不支持，请使用 PNG、JPG 或 WEBP 图片。',
      suggestion:
        '检查本镜引用的角色/场景/道具封面是否为 PNG/JPG/WEBP。若仍是 SVG 占位图，请对该资产重新生图或上传位图后再生成视频。',
    }
  }

  // 参考图槽位收到非图片（常见：音色 mp3 误进 image_url）
  if (/invalid image format|参考图格式无效/i.test(text)) {
    const idx = extractContentIndex(text)
    const where =
      (idx != null ? `提交内容第 ${idx + 1} 项 / content[${idx}]` : '某张参考图')
    return {
      title: '参考图格式无效',
      message: `视频服务按图片校验失败：${where}。这不是参考音频时长问题。`,
      suggestion:
        '请检查本镜角色/场景封面是否为 PNG/JPG/WEBP；若画布上挂了独立音色节点，确认它只作参考音频、不要占用参考图。修好素材后重新生成该分镜。',
    }
  }

  if (/Seedance create error\s*400|Kie createTask error/i.test(text)) {
    const idx = extractContentIndex(text)
    const where =
      (idx != null ? `（提交内容第 ${idx + 1} 项 / content[${idx}]）` : '')
    return {
      title: '视频服务拒绝请求',
      message: `上游返回参数或内容错误，未能创建生成任务${where}。`,
      suggestion:
        '优先检查本镜参考图是否为可访问的 PNG/JPG/WEBP；若错误原文含 audio duration 再查参考音频是否 ≥ 1.8 秒。持续失败请联系客服并提供任务号。',
    }
  }

  if (/Seedance|上游生成失败/i.test(text)) {
    return {
      title: '视频生成失败',
      message: '视频生成未能完成，请检查参考素材与文案。',
      suggestion: '可稍后重试该分镜；连续失败时请更换参考图或简化脚本。',
    }
  }

  if (/跳过重复任务|分镜已生成完成/.test(text)) {
    return {
      title: '旧任务已跳过',
      message: '调度器发现该分镜已有成片，因此取消了这条重复入队的旧任务。',
      suggestion:
        '若你是在「重新生成」，请看队列里是否还有进行中的新任务；没有的话再点一次重新生成。不要把这条旧取消当成当前失败。',
    }
  }

  if (/已取消|任务已中断/.test(text)) {
    return {
      title: text.includes('取消') ? '已取消' : '任务已中断',
      message: text.includes('取消') ? '任务已取消。' : '任务已中断。',
      suggestion: '需要成片时请重新入队生成。',
    }
  }

  // 成片下载被对端掐断（TokenFree content / CDN），不是参考图或脚本问题
  if (code === 'media_download_failed' || /RemoteProtocolError|peer closed|complete message body|incomplete download|成片下载中断/i.test(text)) {
    return {
      title: '成片下载中断',
      message: '视频已在上游生成，但下载到服务器时连接被中断。',
      suggestion: '系统会沿原任务自动重试下载，请勿重新生成该节点。',
    }
  }

  // 浏览器层断连：不是脚本/参考图问题，常因后端热重载或瞬时网络
  if (/failed to fetch|networkerror|load failed|connection reset|连接被重置/i.test(text)) {
    return {
      title: '网络中断',
      message: '请求后端失败（Failed to fetch）。任务可能仍在排队或已完成。',
      suggestion: '请刷新画布查看节点是否已有成片；没有的话再点一次生成。开发时后端 --reload 热重载也容易触发此提示。',
    }
  }

  return {
    title: '生成失败',
    message: '生成未能完成，请查看任务记录或联系客服。',
    suggestion: '请检查本镜参考图与脚本后重试。',
  }
}

/** 弹窗展示生成失败（含上游欠费 / 用户余额不足等） */
export async function alertDramaGenError(raw: unknown): Promise<void> {
  const text = raw instanceof Error ? raw.message : String(raw || '')
  const code = raw && typeof raw === 'object' && 'code' in raw && typeof raw.code === 'string' ? raw.code : undefined
  const view = formatDramaGenError(text, code)
  const body = [view.message, view.suggestion].filter(Boolean).join('\n\n')
  await dialog.alert({
    title: view.title,
    message: body || view.title,
    tone: 'danger',
  })
}
