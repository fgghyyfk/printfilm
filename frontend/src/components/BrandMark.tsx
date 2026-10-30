import { Link } from 'react-router-dom'
import { BRAND_NAME, BRAND_NAME_EN } from '../lib/siteLinks'

type Props = {
  to?: string
}

/** 顶栏品牌标：图标 + 中文名 + 英文小字 */
export default function BrandMark({ to = '/' }: Props) {
  return (
    <Link to={to} className="brand-mark" aria-label={`${BRAND_NAME} ${BRAND_NAME_EN}`}>
      <img src="/logo.svg" alt="" className="brand-logo" width={30} height={30} />
      <span className="brand-word">
        {BRAND_NAME}
        <small>{BRAND_NAME_EN}</small>
      </span>
    </Link>
  )
}
