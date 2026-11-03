import { useNavigate } from 'react-router-dom'
import { ArrowRight, ShoppingBag } from 'lucide-react'
import { useI18n } from '../../i18n'
import './daihuo.css'

/** 创作页「商品带货」醒目入口：点击进入 /daihuo */
export default function DaihuoEntryCard() {
  const { t } = useI18n()
  const nav = useNavigate()
  return (
    <button type="button" className="pf-daihuo-entry" onClick={() => nav('/daihuo')}>
      <span className="pf-daihuo-entry-icon" aria-hidden>
        <ShoppingBag size={20} strokeWidth={1.8} />
      </span>
      <span className="pf-daihuo-entry-body">
        <strong>{t('daihuo.entry.title')}</strong>
        <span>{t('daihuo.entry.desc')}</span>
      </span>
      <span className="pf-daihuo-entry-cta">
        {t('daihuo.entry.cta')}
        <ArrowRight size={14} aria-hidden />
      </span>
    </button>
  )
}
