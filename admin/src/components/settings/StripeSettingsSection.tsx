import { LabeledControl, SettingsPanel } from "@/components/settings/SettingsPanel";
import { SecretField } from "@/components/settings/SecretField";
import type { AdminModelSettings } from "@/api/types/settings";
import type { StripeSecretState } from "@/lib/stripeSettings";

type Props = {
  form: AdminModelSettings;
  patchField: <K extends keyof AdminModelSettings>(key: K, value: AdminModelSettings[K]) => void;
  secrets: StripeSecretState;
  onSecretsChange: (next: StripeSecretState) => void;
};

// 充值通道切换 + Stripe（支付宝 / 微信经 Stripe 托管收银台）配置
export function StripeSettingsSection({ form, patchField, secrets, onSecretsChange }: Props) {
  const isCny = (form.stripe_currency || "cny").trim().toLowerCase() === "cny";
  return (
    <SettingsPanel
      className="settings-panel--compact"
      title="充值通道 · Stripe"
      description="Webhook 地址：{域名}/api/billing/stripe/webhook，订阅 checkout.session.completed 与 async_payment_succeeded"
    >
      <div className="settings-field-grid">
        <LabeledControl label="当前充值通道" hint="用户端支付宝 / 微信按钮走所选通道" className="settings-field-span-full">
          <select
            className="settings-select"
            value={form.payment_provider || "epay"}
            onChange={(e) => patchField("payment_provider", e.target.value as AdminModelSettings["payment_provider"])}
          >
            <option value="epay">易支付（聚合扫码）</option>
            <option value="stripe">Stripe（支付宝 / 微信支付）</option>
          </select>
        </LabeledControl>
        <SecretField
          label="Secret Key"
          placeholder="sk_live_… / sk_test_…"
          value={secrets.secretKey}
          configured={form.has_stripe_secret_key && !secrets.clearSecretKey}
          onChange={(v) => onSecretsChange({ ...secrets, secretKey: v })}
          onClear={() => onSecretsChange({ ...secrets, secretKey: "", clearSecretKey: true })}
        />
        <SecretField
          label="Webhook 签名密钥"
          placeholder="whsec_…"
          value={secrets.webhookSecret}
          configured={form.has_stripe_webhook_secret && !secrets.clearWebhookSecret}
          onChange={(v) => onSecretsChange({ ...secrets, webhookSecret: v })}
          onClear={() => onSecretsChange({ ...secrets, webhookSecret: "", clearWebhookSecret: true })}
        />
        <LabeledControl label="收款币种" hint="cny 按套餐原价；hkd 等需填汇率">
          <input
            className="settings-input"
            placeholder="cny"
            value={form.stripe_currency}
            onChange={(e) => patchField("stripe_currency", e.target.value)}
          />
        </LabeledControl>
        <LabeledControl label="汇率（1 元人民币 = ?）" hint={isCny ? "币种为 cny 时不生效" : "如 HKD 填 1.08"}>
          <input
            className="settings-input"
            type="number"
            step="0.0001"
            min={0}
            disabled={isCny}
            value={form.stripe_fx_rate}
            onChange={(e) => patchField("stripe_fx_rate", Number(e.target.value) || 0)}
          />
        </LabeledControl>
        <LabeledControl label="支付成功跳转" hint="留空：{站点}/pricing?paid=1" className="settings-field-span-full">
          <input
            className="settings-input"
            value={form.stripe_success_url}
            onChange={(e) => patchField("stripe_success_url", e.target.value)}
          />
        </LabeledControl>
        <LabeledControl label="取消支付跳转" hint="留空：{站点}/pricing" className="settings-field-span-full">
          <input
            className="settings-input"
            value={form.stripe_cancel_url}
            onChange={(e) => patchField("stripe_cancel_url", e.target.value)}
          />
        </LabeledControl>
      </div>
    </SettingsPanel>
  );
}
