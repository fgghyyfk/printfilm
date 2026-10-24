/** 获取时才返回原始 token；续租响应只包含更新后的公共租约字段。 */
export type EditLease = { token: string; version: number; expires_at: string; owner_user_id: number; owner_name: string }
/** 续租更新期限和版本，同时保留当前写入凭证。 */
export function renewedLease(current: EditLease, renewal: Omit<EditLease, 'token'>): EditLease { return { ...current, ...renewal, token: current.token } }
/** 迟到的释放/失败回调只能操作自己持有的那一代 token。 */
export function isSameLease(current: { token: string } | undefined, token: string): boolean { return Boolean(current && current.token === token) }
