import { Link } from 'react-router-dom'
import { useI18n } from '../i18n'
import { BRAND_NAME_EN, brandFullName, brandName } from '../lib/siteLinks'

type Props = {
  to?: string
}

/** 顶栏品牌标：中文界面为图标 + 中文名 + 英文小字，英文界面只显示英文名 */
export default function BrandMark({ to = '/' }: Props) {
  const { locale } = useI18n()
  return (
    <Link to={to} className="brand-mark" aria-label={brandFullName(locale)}>
      <img src="/logo.svg" alt="" className="brand-logo" width={30} height={30} />
      <span className="brand-word">
        {brandName(locale)}
        {locale === 'en' ? null : <small>{BRAND_NAME_EN}</small>}
      </span>
    </Link>
  )
}
