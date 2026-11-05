import { useI18n } from '../../i18n'
import { skuNamesForFiles } from '../../lib/ecomSku'
import ImageUpload from './ImageUpload'

type Props = { files: File[]; names: string[]; maxImages: number; disabled: boolean; onChange: (files: File[], names: string[]) => void }

/** 复用商品上传，按文件默认命名并在提交前编辑每个 SKU 的名称。 */
export default function SkuProductUpload({ files, names, maxImages, disabled, onChange }: Props) {
  const { t } = useI18n()
  return <>
    <ImageUpload files={files} maxImages={maxImages} disabled={disabled}
      onChange={(next) => onChange(next, skuNamesForFiles(next, files, names))} />
    <div className="pf-job-params">
      {files.map((file, index) => <label key={index}>
        {t('ecomJobs.sku.name', { n: index + 1 })} · {file.name}
        <input type="text" maxLength={100} disabled={disabled} value={names[index] || ''}
          onChange={(event) => onChange(files, names.map((name, i) => i === index ? event.target.value : name))} />
      </label>)}
    </div>
  </>
}
