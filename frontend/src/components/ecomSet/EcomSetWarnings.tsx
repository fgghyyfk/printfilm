import { useI18n } from '../../i18n'

/** 后端返回的规划提示（极限词替换、未识别图片等） */
export default function EcomSetWarnings({ warnings }: { warnings: string[] }) {
  const { t } = useI18n()
  if (!warnings.length) return null
  return (
    <div className="pf-ecom-warnings" role="note">
      <strong>{t('ecomSet.plan.warnings')}</strong>
      <ul>
        {warnings.map((text, i) => (
          <li key={i}>{text === '本地示意翻译，未调用模型' ? t('ecomSet.translate.mock') : text}</li>
        ))}
      </ul>
    </div>
  )
}
