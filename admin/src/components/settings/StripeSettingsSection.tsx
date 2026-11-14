import { LabeledControl, SettingsPanel } from "@/components/settings/SettingsPanel";
import { SecretField } from "@/components/settings/SecretField";
import { SettingsSelect } from "@/components/settings/SettingsSelect";
import type { AdminModelSettings } from "@/api/types/settings";
import type { StripeSecretState } from "@/lib/stripeSettings";

type Props = {
  form: AdminModelSettings;
  patchField: <K extends keyof AdminModelSettings>(key: K, value: AdminModelSettings[K]) => void;
  secrets: StripeSecretState;
  onSecretsChange: (next: StripeSecretState) => void;
};

const PROVIDER_OPTIONS = [
  { value: "epay" as const, label: "易支付（聚合扫码）" },
  { value: "stripe" as const, label: "Stripe（支付宝 / 微信支付）" },
];

const CURRENCY_OPTIONS = [
  { value: "cny", label: "人民币 CNY（按套餐原价）" },
  { value: "hkd", label: "港币 HKD（按汇率折算）" },
  { value: "usd", label: "美元 USD（按汇率折算，仅支付宝）" },
];

// 充值通道切换 + Stripe（支付宝 / 微信经 Stripe 托管收银台）配置
export function StripeSettingsSection({ form, patchField, secrets, onSecretsChange }: Props) {
  const isStripe = form.payment_provider === "stripe";
  const isCny = (form.stripe_currency || "cny").trim().toLowerCase() === "cny";
  return (
    <SettingsPanel
      title="充值通道"
      description="用户端的支付宝 / 微信按钮走这里选的通道。Stripe 密钥不回显，输入新值后保存即可替换。"
    >
      <div className="settings-field-grid">
        <LabeledControl label="当前充值通道" hint="切到 Stripe 前请先填好下方两个密钥。">
          <SettingsSelect
            value={form.payment_provider || "epay"}
            options={PROVIDER_OPTIONS}
            onChange={(value) => patchField("payment_provider", value)}
          />
        </LabeledControl>
        <LabeledControl label="收款币种" hint="默认人民币；香港账户也可用港币并填写汇率。">
          <SettingsSelect
            value={(form.stripe_currency || "cny").toLowerCase()}
            options={CURRENCY_OPTIONS}
            onChange={(value) => patchField("stripe_currency", value)}
          />
        </LabeledControl>
        <SecretField
          label="Stripe Secret Key"
          placeholder="sk_live_…"
          value={secrets.secretKey}
          configured={form.has_stripe_secret_key}
          required={isStripe}
          clearPending={secrets.clearSecretKey}
          onUndoClear={() => onSecretsChange({ ...secrets, clearSecretKey: false })}
          onChange={(v) => onSecretsChange({ ...secrets, secretKey: v, clearSecretKey: false })}
          onClear={() => onSecretsChange({ ...secrets, secretKey: "", clearSecretKey: true })}
        />
        <SecretField
          label="Webhook 签名密钥"
          hint="Stripe → Webhooks → 本站端点详情里的 Signing secret。"
          placeholder="whsec_…"
          value={secrets.webhookSecret}
          configured={form.has_stripe_webhook_secret}
          required={isStripe}
          clearPending={secrets.clearWebhookSecret}
          onUndoClear={() => onSecretsChange({ ...secrets, clearWebhookSecret: false })}
          onChange={(v) => onSecretsChange({ ...secrets, webhookSecret: v, clearWebhookSecret: false })}
          onClear={() => onSecretsChange({ ...secrets, webhookSecret: "", clearWebhookSecret: true })}
        />
      </div>
      <details className="settings-advanced mt-5">
        <summary>高级 · 汇率、跳转地址与 Webhook</summary>
        <div className="settings-field-grid">
          <LabeledControl label="汇率（1 元人民币 = ?）" optional hint={isCny ? "币种为人民币时不生效。" : "例如港币填 1.08。"}>
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
          <LabeledControl label="Webhook 地址" optional hint="在 Stripe 订阅 checkout.session.completed 与 checkout.session.async_payment_succeeded。">
            <input className="settings-input" readOnly value="https://{域名}/api/billing/stripe/webhook" />
          </LabeledControl>
          <LabeledControl label="支付成功跳转" optional hint="留空回到 {站点}/pricing?paid=1。">
            <input className="settings-input" type="url" value={form.stripe_success_url} onChange={(e) => patchField("stripe_success_url", e.target.value)} autoCapitalize="none" spellCheck={false} />
          </LabeledControl>
          <LabeledControl label="取消支付跳转" optional hint="留空回到 {站点}/pricing。">
            <input className="settings-input" type="url" value={form.stripe_cancel_url} onChange={(e) => patchField("stripe_cancel_url", e.target.value)} autoCapitalize="none" spellCheck={false} />
          </LabeledControl>
        </div>
      </details>
    </SettingsPanel>
  );
}
