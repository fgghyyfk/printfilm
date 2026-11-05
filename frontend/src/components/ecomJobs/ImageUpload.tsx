import { useEffect, useState } from 'react'
import { useI18n } from '../../i18n'
import { validateEcomImages } from '../../lib/ecomJobs'
import Button from '../ui/Button'

type Props = { files: File[]; maxImages: number; disabled: boolean; role?: string; labelKey?: string; onChange: (files: File[]) => void }

/** 多图上传与本地预览，整批校验数量和格式并及时释放预览地址。 */
export default function ImageUpload({ files, maxImages, disabled, role = 'product', labelKey, onChange }: Props) {
  const { t } = useI18n()
  /* previews 为当前文件的临时地址；error 为上传校验结果。 */
  const [previews, setPreviews] = useState<string[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file))
    setPreviews(urls)
    return () => urls.forEach((url) => URL.revokeObjectURL(url))
  }, [files])

  // 追加选中的完整批次；错误时保留此前已选图片。
  function addFiles(selected: File[]) {
    const next = [...files, ...selected]
    const problem = validateEcomImages(next, maxImages)
    setError(problem ? t(`ecomJobs.errors.${problem}`, { max: maxImages }) : '')
    if (!problem) onChange(next)
  }

  return (
    <div className="pf-job-upload">
      <label className="pf-job-upload-label">
        <strong>{t(labelKey || (role === 'style' ? 'ecomJobs.style.reference' : role === 'model' || role === 'scene' ? `ecomJobs.buyerShow.${role}` : 'ecomJobs.images'))}</strong>
        <span className="pf-muted">{t('ecomJobs.uploadHint', { max: maxImages, count: files.length })}</span>
        <input type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={disabled}
          onChange={(event) => { addFiles(Array.from(event.target.files || [])); event.target.value = '' }} />
      </label>
      {error ? <p role="alert" className="pf-tool-error">{error}</p> : null}
      <div className="pf-job-upload-grid">
        {files.map((file, index) => (
          <div className="pf-job-upload-item" key={`${file.name}-${index}`}>
            {previews[index] ? <img src={previews[index]} alt={file.name} /> : null}
            <small>{t(`ecomJobs.roles.${role}`)} · {file.name}</small>
            <Button type="button" size="sm" variant="text" disabled={disabled} onClick={() => { onChange(files.filter((_, i) => i !== index)); setError('') }}>
              {t('ecomJobs.remove')}
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}
