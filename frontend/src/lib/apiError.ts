import { translate as translateUi } from '../i18n/runtime'
import { notifyBillingErrorIfNeeded } from './billingError'
import { apiErrorMessage } from '../api/http'

/** 解析 FastAPI detail 并抛出；402 / 余额不足时弹出充值引导 */
export function throwApiError(status: number, detail: unknown, fallback = translateUi("workflow.requestFailed"), code?: string | null): never {
  const finalMessage = apiErrorMessage(detail, fallback)
  notifyBillingErrorIfNeeded(status, finalMessage)
  throw Object.assign(new Error(finalMessage), { status, code: code || undefined })
}
