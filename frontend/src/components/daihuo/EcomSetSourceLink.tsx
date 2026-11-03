import { Link } from 'react-router-dom'
import { ShoppingBag } from 'lucide-react'
import { useI18n } from '../../i18n'
import './daihuo.css'

/** 分镜页反向入口：项目画面来自商品套图时，链接回 /daihuo?id= */
export default function EcomSetSourceLink({ ecomSetId }: { ecomSetId: number | null | undefined }) {
  const { t } = useI18n()
  if (!ecomSetId) return null
  return (
    <p className="pf-daihuo-source">
      <ShoppingBag size={14} aria-hidden />
      {t('daihuo.source.text')}
      {' · '}
      <Link to={`/daihuo?id=${ecomSetId}`}>{t('daihuo.source.link')}</Link>
    </p>
  )
}
