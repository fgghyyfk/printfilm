import { useI18n } from '../../i18n'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import Modal from '../ui/Modal'
import PaymentBrandIcon from './PaymentBrandIcon'
import { api } from '../../api'

export type PayCheckout = {
  out_trade_no: string
  sku_id: string
  sku_name: string
  pay_type: 'alipay' | 'wxpay' | string
  amount_fen: number
  credit_fen: number
  /** qr=弹窗扫码；redirect=新开易支付收银台 */
  pay_mode?: 'qr' | 'redirect' | string
  qr_payload: string
  payurl?: string
  img?: string
  expire_seconds?: number
}

type Props = {
  open: boolean
  checkout: PayCheckout | null
  onClose: () => void
  onPaid: () => void
}

/** 将分转换为支付弹窗显示的两位小数金额。 */
function yuan(fen: number) {
  return (fen / 100).toFixed(2)
}

/** 将剩余秒数格式化为分秒倒计时。 */
function formatRemain(sec: number) {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** 扫码支付弹窗：展示二维码或等待收银台回跳，并轮询订单状态 */
export default function PaymentModal({ open, checkout, onClose, onPaid }: Props) {
  const { t: translateUi } = useI18n()
  /*
   * qrDataUrl 二维码 data URL
   * remain 剩余秒数
   * status 当前状态文案
   * checking 手动确认中
   */
  const [qrDataUrl, setQrDataUrl] = useState('')
  const [remain, setRemain] = useState(300)
  const [status, setStatus] = useState<'waiting' | 'paid' | 'expired'>('waiting')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')

  // 轮询和手动确认都读取最近一次提交的回调，父组件重渲染不会重启订单计时。
  const onPaidRef = useRef(onPaid)
  useLayoutEffect(() => {
    onPaidRef.current = onPaid
  }, [onPaid])

  /** 取消支付：关闭弹窗，并尽量把待支付订单置为 closed */
  async function handleCancel() {
    const tradeNo = checkout?.out_trade_no
    if (tradeNo && status !== 'paid') {
      try {
        await api.closeBillingOrder(tradeNo)
      } catch {
        /* 忽略关闭失败，仍允许退出弹窗 */
      }
    }
    onClose()
  }

  /** 新开易支付收银台（支付宝无原生码时） */
  function openCashier() {
    const url = (checkout?.payurl || '').trim()
    if (!url) {
      setError(translateUi("workflow.paymentLinkUnavailablePleaseTryAgainLater"))
      return
    }
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  useEffect(() => {
    if (!open || !checkout) return
    /*
     * expiredAt 过期时间戳
     * isRedirect 是否收银台跳转模式
     * payload 需编码为二维码的内容
     */
    const expiredAt = Date.now() + (checkout.expire_seconds ?? 300) * 1000
    const payurl = (checkout.payurl || '').trim()
    const isRedirect = checkout.pay_mode === 'redirect' || (!checkout.qr_payload && !!payurl)
    setRemain(checkout.expire_seconds ?? 300)
    setStatus('waiting')
    setError('')
    setChecking(false)
    setQrDataUrl('')

    const payload = checkout.qr_payload || checkout.img || ''
    let cancelled = false

    /** 按网关返回值展示二维码图片、编码二维码或打开收银台。 */
    async function paintQr() {
      if (isRedirect) {
        // 无原生二维码：直接新开易支付站点，弹窗仅等待到账
        if (!cancelled && payurl) {
          window.open(payurl, '_blank', 'noopener,noreferrer')
        }
        return
      }
      if (!payload) {
        setQrDataUrl('')
        setError(translateUi("workflow.paymentQrCodeUnavailablePleaseTryAgainLater"))
        return
      }
      // 若网关直接返回图片 URL，优先使用
      if (/^https?:\/\//i.test(payload) && /\.(png|jpe?g|gif|webp)(\?|$)/i.test(payload)) {
        if (!cancelled) setQrDataUrl(payload)
        return
      }
      try {
        const url = await QRCode.toDataURL(payload, {
          width: 220,
          margin: 2,
          color: { dark: '#111318', light: '#ffffff' },
          errorCorrectionLevel: 'M',
        })
        if (!cancelled) setQrDataUrl(url)
      } catch {
        if (!cancelled) setError(translateUi("workflow.couldNotCreateTheQrCode"))
      }
    }

    void paintQr()

    const tick = window.setInterval(() => {
      const left = Math.max(0, Math.ceil((expiredAt - Date.now()) / 1000))
      setRemain(left)
      if (left <= 0) {
        setStatus('expired')
        window.clearInterval(tick)
        // 倒计时结束：后端自动关闭（拉取一次触发过期清理）
        void api.getBillingOrder(checkout.out_trade_no).catch(() => undefined)
      }
    }, 250)

    const poll = window.setInterval(async () => {
      if (!checkout.out_trade_no) return
      try {
        const order = await api.getBillingOrder(checkout.out_trade_no)
        if (order.status === 'paid') {
          setStatus('paid')
          window.clearInterval(poll)
          window.clearInterval(tick)
          onPaidRef.current()
        } else if (order.status === 'closed') {
          setStatus('expired')
          window.clearInterval(poll)
          window.clearInterval(tick)
        }
      } catch {
        /* ignore transient poll errors */
      }
    }, 2000)

    return () => {
      cancelled = true
      window.clearInterval(tick)
      window.clearInterval(poll)
    }
  }, [open, checkout])

  /** 主动查询订单到账状态，与后台轮询共用最新支付成功回调。 */
  async function confirmPaid() {
    if (!checkout) return
    setChecking(true)
    setError('')
    try {
      const order = await api.getBillingOrder(checkout.out_trade_no)
      if (order.status === 'paid') {
        setStatus('paid')
        onPaidRef.current()
      } else {
        setError(
          checkout.pay_mode === 'redirect'
            ? translateUi("workflow.paymentHasNotBeenConfirmedCompleteItOnThe")
            : translateUi("workflow.paymentHasNotBeenConfirmedTryAgainLaterOr"),
        )
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : translateUi("workflow.couldNotCheckTheStatus"))
    } finally {
      setChecking(false)
    }
  }

  if (!checkout) return null

  const isAlipay = checkout.pay_type === 'alipay'
  const isRedirect = checkout.pay_mode === 'redirect' || (!checkout.qr_payload && !!checkout.payurl)
  const title = isRedirect
    ? isAlipay
      ? translateUi("workflow.payWithAlipay")
      : translateUi("workflow.wechatPay")
    : isAlipay
      ? translateUi("workflow.scanWithAlipay")
      : translateUi("workflow.scanWithWechatPay")
  const tip = isRedirect
    ? translateUi("workflow.thePaymentPageIsOpenCompletePaymentInThe")
    : isAlipay
      ? translateUi("workflow.scanTheCodeWithAlipayToPay")
      : translateUi("workflow.scanTheCodeWithWechatPayToPay")

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={status !== 'waiting'}
      className="pf-pay-modal"
      size="md"
    >
      <div className="pf-pay-sheet">
        <header className="pf-pay-sheet-head">
          <div className="pf-pay-sheet-title">
            <PaymentBrandIcon brand={isAlipay ? 'alipay' : 'wxpay'} size="md" />
            <strong>{title}</strong>
          </div>
          <button type="button" className="pf-pay-sheet-close" onClick={() => void handleCancel()} aria-label={translateUi("workflow.close")}>
            ×
          </button>
        </header>

        <div className="pf-pay-sku-box">
          <strong>{checkout.sku_name}</strong>
          <span>{translateUi("workflow.credited", { v1: yuan(checkout.credit_fen) })}</span>
        </div>

        <div className="pf-pay-amount">
          <span>{translateUi("workflow.amountToPay")}</span>
          <em>¥{yuan(checkout.amount_fen)}</em>
        </div>

        <div className="pf-pay-qr-wrap">
          {isRedirect ? (
            <div className="pf-pay-qr is-empty pf-pay-redirect-box">
              <p>{translateUi("workflow.thePaymentPageHasOpenedInANewWindow")}</p>
              <button type="button" className="pf-pay-btn primary" onClick={openCashier}>
                {translateUi("workflow.reopenPaymentPage")}
              </button>
            </div>
          ) : qrDataUrl ? (
            <div className="pf-pay-qr">
              <img src={qrDataUrl} alt={translateUi("workflow.paymentQrCode")} width={220} height={220} />
              <span className={`pf-pay-qr-badge ${isAlipay ? 'alipay' : 'wxpay'}`} aria-hidden />
            </div>
          ) : (
            <div className="pf-pay-qr is-empty">{error || translateUi("workflow.loadingQrCode")}</div>
          )}
          <p className="pf-pay-tip">{tip}</p>
          <p className={`pf-pay-expire${status === 'expired' ? ' is-expired' : ''}`}>
            <span className="pf-pay-clock" aria-hidden />
            {status === 'expired'
              ? isRedirect
                ? translateUi("workflow.thisOrderHasExpiredCloseItAndStartA")
                : translateUi("workflow.thisQrCodeHasExpiredCloseItAndStart")
              : isRedirect
                ? translateUi("workflow.completePaymentWithin", { v1: String(formatRemain(remain)) })
                : translateUi("workflow.thisQrCodeExpiresIn", { v1: String(formatRemain(remain)) })}
          </p>
          <p className={`pf-pay-wait${status === 'paid' ? ' is-paid' : ''}`}>
            <span className="pf-pay-dot" aria-hidden />
            {status === 'paid'
              ? translateUi("workflow.paymentReceivedYourBalanceWillUpdateShortly")
              : status === 'expired'
                ? translateUi("workflow.orderTimedOut")
                : translateUi("workflow.waitingForPaymentConfirmationKeepThisPageOpen")}
          </p>
        </div>

        {error ? <p className="pf-error pf-pay-error">{error}</p> : null}

        <div className="pf-pay-actions">
          <button type="button" className="pf-pay-btn ghost" onClick={() => void handleCancel()}>
            {translateUi("workflow.cancelPayment")}
          </button>
          <button
            type="button"
            className="pf-pay-btn primary"
            disabled={checking || status === 'paid' || status === 'expired'}
            onClick={() => void confirmPaid()}
          >
            {checking ? translateUi("workflow.confirming") : status === 'paid' ? translateUi("workflow.credited_ce9b10") : translateUi("workflow.iVeCompletedPayment")}
          </button>
        </div>

        <p className="pf-pay-secure-line">
          <span className="pf-pay-shield" aria-hidden />
          {translateUi("workflow.paymentIsHandledByEpayCreditsAreAddedAfter")}
        </p>
      </div>
    </Modal>
  )
}
