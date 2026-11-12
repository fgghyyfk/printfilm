import type { AdminModelSettings } from "@/api/types/settings";

/** Stripe 两个密钥的本地输入与清除标记，由父面板持有以便统一保存 */
export type StripeSecretState = {
  secretKey: string;
  clearSecretKey: boolean;
  webhookSecret: string;
  clearWebhookSecret: boolean;
};

export const EMPTY_STRIPE_SECRETS: StripeSecretState = {
  secretKey: "",
  clearSecretKey: false,
  webhookSecret: "",
  clearWebhookSecret: false,
};

/** 判断 Stripe 是否可用：需要 Secret Key 与 Webhook 签名密钥 */
export function stripeReady(form: AdminModelSettings, secrets: StripeSecretState): boolean {
  const hasKey = (form.has_stripe_secret_key && !secrets.clearSecretKey) || secrets.secretKey.trim().length > 0;
  const hasHook =
    (form.has_stripe_webhook_secret && !secrets.clearWebhookSecret) || secrets.webhookSecret.trim().length > 0;
  return hasKey && hasHook;
}

/** 组装保存 Stripe 配置的 PATCH 字段 */
export function stripePatchBody(form: AdminModelSettings, secrets: StripeSecretState): Record<string, unknown> {
  return {
    payment_provider: form.payment_provider,
    stripe_secret_key: secrets.secretKey.trim() || undefined,
    clear_stripe_secret_key: secrets.clearSecretKey,
    stripe_webhook_secret: secrets.webhookSecret.trim() || undefined,
    clear_stripe_webhook_secret: secrets.clearWebhookSecret,
    stripe_currency: (form.stripe_currency || "cny").trim().toLowerCase(),
    stripe_fx_rate: form.stripe_fx_rate,
    stripe_success_url: form.stripe_success_url,
    stripe_cancel_url: form.stripe_cancel_url,
  };
}
