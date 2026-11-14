import type { ReactNode } from 'react'
import { AlertCircle, CheckCircle2, Info, LoaderCircle, TriangleAlert, WifiOff, X } from 'lucide-react'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import './dramaStatusNotice.css'

type Props = {
  tone?: 'info' | 'success' | 'warning' | 'error' | 'busy'
  title: string
  children?: ReactNode
  icon?: ReactNode
  className?: string
  onDismiss?: () => void
}

const icons = {
  info: Info,
  success: CheckCircle2,
  warning: TriangleAlert,
  error: AlertCircle,
  busy: LoaderCircle,
}

/** 大纲页共用状态条：保留内容层级，生成中的状态使用轻量动效。 */
export default function DramaStatusNotice({ tone = 'info', title, children, icon, className = '', onDismiss }: Props) {
  const Icon = icons[tone]
  return (
    <div className={`drama-status-notice is-${tone} ${className}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span className="drama-status-notice-icon" aria-hidden="true">
        {icon || <Icon size={17} strokeWidth={1.8} className={tone === 'busy' ? 'drama-status-notice-spinner' : undefined} />}
      </span>
      <div className="drama-status-notice-content">
        <strong className="drama-status-notice-title">{title}</strong>
        {children ? <div className="drama-status-notice-description">{children}</div> : null}
      </div>
      {onDismiss ? (
        <button type="button" className="drama-status-notice-close" onClick={onDismiss} aria-label="关闭提示">
          <X size={16} aria-hidden="true" />
        </button>
      ) : null}
    </div>
  )
}

/** 网络异常使用中文说明，业务错误继续保留余额不足等操作入口。 */
export function DramaErrorNotice({ message, className, onDismiss }: { message: string; className?: string; onDismiss?: () => void }) {
  const networkError = /failed to fetch|networkerror|network request failed|load failed|connection reset|连接被重置/i.test(message)
  return (
    <DramaStatusNotice
      tone={networkError ? 'warning' : 'error'}
      title={networkError ? '暂时无法连接服务' : '操作未完成'}
      icon={networkError ? <WifiOff size={17} strokeWidth={1.8} /> : undefined}
      className={className}
      onDismiss={onDismiss}
    >
      {networkError ? (
        <>
          <p>请检查网络连接后重试。如有生成任务进行中，可稍后查看任务状态。</p>
          <details className="drama-status-notice-details">
            <summary>查看错误详情</summary>
            <p>{message}</p>
          </details>
        </>
      ) : <BillingErrorNotice message={message} className="drama-status-notice-message" inline />}
    </DramaStatusNotice>
  )
}
