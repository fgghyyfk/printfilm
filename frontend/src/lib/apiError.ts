import { notifyBillingErrorIfNeeded } from './billingError'
import { apiErrorMessage } from '../api/http'
import { workspaceScope } from '../features/teams/workspaceScope'

/** 解析 FastAPI detail 并抛出；402 / 余额不足时弹出充值引导 */
export function throwApiError(status: number, detail: unknown, fallback = '请求失败'): never {
  const finalMessage = apiErrorMessage(detail, fallback)
  if (workspaceScope.teamId === null) notifyBillingErrorIfNeeded(status, finalMessage)
  throw Object.assign(new Error(finalMessage), { status })
}
