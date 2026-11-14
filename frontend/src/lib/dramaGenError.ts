import { translate as translateUi } from '../i18n/runtime'
/** 漫剧生成队列：把上游/平台原始错误翻成可读中文，并附处理建议 */

import { dialog } from './dialog'
import { isBillingError } from './billingError'
import { formatCopyrightError } from './generationError.ts'

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
  if (formatCopyrightError(text)) return true
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
      title: translateUi("workflow.costAwaitingReview"),
      message: translateUi("workflow.thisCallSResultAndCostAreAwaitingReview"),
      suggestion: translateUi("workflow.keepTheTaskRecordAndContactSupportForReview"),
    }
  }
  if (code === 'recovery_limit' || /任务执行中断已达 \d+ 次/.test(text)) {
    const count = text.match(/已达 (\d{1,6}) 次/)
    return {
      title: translateUi("workflow.automaticRecoveryStopped"),
      message: count ? translateUi("workflow.thisTaskHasBeenInterruptedTimesAutomaticRecoveryHas", { v1: String(Number(count[1])) }) : translateUi("workflow.thisTaskHasBeenInterruptedRepeatedlyAutomaticRecoveryHas"),
      suggestion: translateUi("workflow.checkTheTaskRecordAndContactTheAdministrator"),
    }
  }
  const copyright = formatCopyrightError(text)
  if (copyright) return copyright
  if (!text) {
    return {
      title: translateUi("ecomSet.result.failed"),
      message: translateUi("workflow.theTaskDidNotFinishAndNoSpecificError"),
      suggestion: translateUi("workflow.tryAgainLaterIfTheIssuePersistsCheckAccess"),
    }
  }

  if (code === 'upstream_timeout' || code === 'upstream_submit_unknown' || /ReadTimeout|WriteTimeout|等待上游超时|响应超时/i.test(text)) {
    return {
      title: translateUi("workflow.providerResponseTimedOut"),
      message: translateUi("workflow.theProviderTimedOutAndTheResultIsUnconfirmed"),
      suggestion:
        translateUi("workflow.waitOrCheckTheTaskRecordToAvoidDuplicate"),
    }
  }

  if (code === 'upstream_unavailable' || /网络错误|ConnectError|ConnectTimeout|无法连接上游|无法连接生成服务|tokenfree\.com/i.test(text)) {
    return {
      title: translateUi("workflow.cannotReachTheImageVideoService"),
      message: translateUi("workflow.theGenerationServiceIsTemporarilyUnreachable"),
      suggestion:
        translateUi("workflow.theServerCannotReachTheProviderCheckTheNetwork"),
    }
  }

  if (/^生图失败$/.test(text)) {
    return {
      title: translateUi("workflow.imageGenerationFailed"),
      message: translateUi("workflow.imageGenerationFailedButThisOlderTaskHasNo"),
      suggestion:
        translateUi("workflow.generateAgainToCaptureAClearerErrorIfIt"),
    }
  }

  if (code === 'upstream_account_unavailable' || isUpstreamAccountError(text) || (/Seedream error 403/i.test(text) && /AccountOverdue/i.test(text))) {
    return {
      title: translateUi("workflow.providerAccountHasInsufficientCredit"),
      message:
        translateUi("workflow.theSeedreamProviderAccountHasInsufficientCreditSoThe"),
      suggestion: translateUi("workflow.askTheSiteAdministratorToTopUpTheTokenfree"),
      upstreamAccountBlocked: true,
    }
  }

  if (code === 'upstream_auth_unavailable') {
    return {
      title: translateUi("workflow.modelServiceAuthenticationUnavailable"),
      message: translateUi("workflow.modelServiceAuthenticationIsTemporarilyUnavailable"),
      suggestion: translateUi("workflow.askTheAdministratorToCheckTheModelChannelConfiguration"),
    }
  }

  if (isBillingError(text)) {
    return {
      title: translateUi("workflow.insufficientBalance"),
      message: translateUi("workflow.yourWalletBalanceIsTooLowToContinueGeneration"),
      suggestion: translateUi("workflow.topUpYourWalletThenRetryTheTask"),
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
        ? translateUi("workflow.submittedItemContentUsuallyACharacterOrSceneReference", { v1: String(idx + 1), v2: String(idx) })
        : translateUi("workflow.aReferenceImage")
    return {
      title: translateUi("workflow.referenceImageMayContainARealPerson"),
      message: translateUi("workflow.theVideoServiceRejectedGenerationBecauseInputImageMay", { v1: String(where) }),
      suggestion:
        translateUi("workflow.openTheAssetsOnTheLeftGenerateAnAnime"),
    }
  }

  if (/重试超过上限|超过重试上限|内部自动重试超过上限/.test(text)) {
    return {
      title: translateUi("workflow.generationFailedAfterRetries"),
      message: translateUi("workflow.thisTaskHasExhaustedItsAutomaticRetriesCheckThe"),
      suggestion:
        translateUi("workflow.theRetriesWithinThisTaskAreExhaustedYouCan"),
    }
  }

  if (/上一镜失败|无法衔接尾帧/.test(text)) {
    return {
      title: translateUi("workflow.cannotContinueFromThePreviousShot"),
      message: translateUi("workflow.thisShotRequiresThePreviousShotSLastFrame"),
      suggestion: translateUi("workflow.fixAndRegenerateThePreviousShotThenGenerateThe"),
    }
  }

  if (code === 'fragment_changed' || /分镜已变更|分镜已更新|分镜上下文丢失|分镜不存在/.test(text)) {
    return {
      title: translateUi("workflow.storyboardUpdated"),
      message: translateUi("workflow.theStoryboardWasSavedOrSplitAgainDuringGeneration"),
      suggestion: translateUi("workflow.returnToTheEpisodeAndGenerateFromTheCurrent"),
    }
  }

  if (code === 'text_moderation' || /分镜文案未通过内容审核/.test(text) || (/InputTextSensitive|text.*sensitive|敏感/i.test(text) && /Seedance|create error/i.test(text))) {
    return {
      title: translateUi("workflow.copyDidNotPassModeration"),
      message: translateUi("workflow.theShotScriptOrPromptTriggeredContentModeration"),
      suggestion: translateUi("workflow.reviseTheSensitiveWordingInTheShotThenTry"),
    }
  }

  if (/resource download failed|audio_url/i.test(text) && !/audio duration/i.test(text)) {
    return {
      title: translateUi("workflow.cannotDownloadTheReferenceAudio"),
      message: translateUi("workflow.theVoiceReferenceUrlIsInvalidOrTemporarilyUnavailable"),
      suggestion: translateUi("workflow.checkTheCharacterSVoicePreviewRegenerateOrSelect"),
    }
  }

  if (code === 'reference_audio_unavailable' || /无法读取参考音频时长|参考音频地址为空|参考音频无法读取/i.test(text)) {
    return {
      title: translateUi("workflow.cannotValidateTheReferenceAudio"),
      message: translateUi("workflow.theReferenceAudioCouldNotBeReadOrIts"),
      suggestion: translateUi("workflow.checkThatTheCharacterNarratorPreviewIsAccessibleUpload"),
    }
  }

  if (/参考音频过长/i.test(text)) {
    return {
      title: translateUi("workflow.referenceAudioIsTooLong"),
      message: translateUi("workflow.trimTheReferenceAudioToNoMoreThan30"),
      suggestion: translateUi("workflow.trimTheCharacterNarratorPreviewBeforeGeneratingTheShot"),
    }
  }

  // Seedance r2v：reference_audio 须 ≥ 1.8 秒（不是参考图）
  if (
    /参考音频过短/i.test(text) ||
    (/audio duration|1\.8/i.test(text) && /audio|音色|reference_audio|content\[/i.test(text))
  ) {
    const idx = extractContentIndex(text)
    const where =
      (idx != null ? translateUi("workflow.submittedItemContentReferenceAudioNotAnImage", { v1: String(idx + 1), v2: String(idx) }) : translateUi("workflow.aCharacterNarratorVoice"))
    return {
      title: translateUi("workflow.referenceAudioIsTooShort"),
      message: translateUi("workflow.theVideoServiceRequiresAtLeast18Seconds", { v1: String(where) }),
      suggestion:
        translateUi("workflow.openTheCharacterOrNarratorAssetOnTheLeft"),
    }
  }

  if (/only support adaptive aspect ratio|adaptive aspect ratio/i.test(text)) {
    return {
      title: translateUi("workflow.incompatibleAspectRatio"),
      message: translateUi("workflow.thisVideoChannelMayRejectAFixedAspectRatio"),
      suggestion: translateUi("workflow.generateTheShotAgainTheServerWillAdaptThe"),
    }
  }

  if (/未开通|无可用渠道|distributor/i.test(text)) {
    return {
      title: translateUi("workflow.imageModelNotEnabled"),
      message: translateUi("workflow.theCurrentModelChannelIsNotEnabled"),
      suggestion: translateUi("workflow.askTheAdministratorToEnableThisModelSuchAs"),
    }
  }

  if (/File type not supported|参考图格式不支持|不支持 SVG/i.test(text)) {
    return {
      title: translateUi("workflow.unsupportedReferenceImageFormat"),
      message: translateUi("workflow.useAPngJpgOrWebpReferenceImage"),
      suggestion:
        translateUi("workflow.checkThatTheCharacterSceneAndPropCoversUse"),
    }
  }

  // 参考图槽位收到非图片（常见：音色 mp3 误进 image_url）
  if (/invalid image format|参考图格式无效/i.test(text)) {
    const idx = extractContentIndex(text)
    const where =
      (idx != null ? translateUi("workflow.submittedItemContent", { v1: String(idx + 1), v2: String(idx) }) : translateUi("workflow.aReferenceImage_e75453"))
    return {
      title: translateUi("workflow.invalidReferenceImageFormat"),
      message: translateUi("workflow.theVideoServiceRejectedAnImageReferenceAudioDuration", { v1: String(where) }),
      suggestion:
        translateUi("workflow.checkThatTheCharacterSceneCoversUsePngJpg"),
    }
  }

  if (/Seedance create error\s*400|Kie createTask error/i.test(text)) {
    const idx = extractContentIndex(text)
    const where =
      (idx != null ? translateUi("workflow.submittedItemContent_527c2b", { v1: String(idx + 1), v2: String(idx) }) : '')
    return {
      title: translateUi("workflow.videoServiceRejectedTheRequest"),
      message: translateUi("workflow.theProviderReportedAParameterOrContentErrorAnd", { v1: String(where) }),
      suggestion:
        translateUi("workflow.firstCheckThatThisShotSReferenceImagesAre"),
    }
  }

  if (/Seedance|上游生成失败/i.test(text)) {
    return {
      title: translateUi("workflow.videoGenerationFailed"),
      message: translateUi("workflow.videoGenerationDidNotFinishCheckTheReferencesAnd"),
      suggestion: translateUi("workflow.tryThisShotAgainLaterForRepeatedFailuresChange"),
    }
  }

  if (/跳过重复任务|分镜已生成完成/.test(text)) {
    return {
      title: translateUi("workflow.olderTaskSkipped"),
      message: translateUi("workflow.theSchedulerFoundThatThisShotAlreadyHasA"),
      suggestion:
        translateUi("workflow.ifYouRequestedRegenerationCheckWhetherANewerTask"),
    }
  }

  if (/已取消|任务已中断/.test(text)) {
    return {
      title: text.includes('取消') ? translateUi("workflow.cancelled") : translateUi("workflow.taskInterrupted"),
      message: text.includes('取消') ? translateUi("workflow.theTaskWasCancelled") : translateUi("workflow.theTaskWasInterrupted"),
      suggestion: translateUi("workflow.queueGenerationAgainWhenYouNeedTheVideo"),
    }
  }

  // 成片下载被对端掐断（TokenFree content / CDN），不是参考图或脚本问题
  if (code === 'media_download_failed' || /RemoteProtocolError|peer closed|complete message body|incomplete download|成片下载中断/i.test(text)) {
    return {
      title: translateUi("workflow.videoDownloadInterrupted"),
      message: translateUi("workflow.theProviderGeneratedTheVideoButTheConnectionWas"),
      suggestion: translateUi("workflow.theSystemWillRetryDownloadingFromTheOriginalTask"),
    }
  }

  // 浏览器层断连：不是脚本/参考图问题，常因后端热重载或瞬时网络
  if (/failed to fetch|networkerror|load failed|connection reset|连接被重置/i.test(text)) {
    return {
      title: translateUi("workflow.networkInterrupted"),
      message: translateUi("workflow.theServerRequestFailedFailedToFetchTheTask"),
      suggestion: translateUi("workflow.refreshTheCanvasToCheckForACompletedVideo"),
    }
  }

  return {
    title: translateUi("ecomSet.result.failed"),
    message: translateUi("workflow.generationDidNotFinishCheckTheTaskRecordOr"),
    suggestion: translateUi("workflow.checkTheShotSReferenceImageAndScriptThen"),
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
