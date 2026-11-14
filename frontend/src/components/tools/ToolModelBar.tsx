import { useState } from 'react'
import { Star, UserRound, UserRoundPlus } from 'lucide-react'
import ToolReferenceAssetPicker from './ToolReferenceAssetPicker'
import type { useToolModels } from '../../hooks/useToolModels'
import { useGenerationText } from '../../lib/generationMedia'

type Props = {
  models: ReturnType<typeof useToolModels>
  disabled?: boolean
}

/**
 * 图片生成页的「模特」选择条：选本次使用的模特（或不使用）、设为默认、从资产库加入或移出模特库。
 * 选中的模特会作为参考图 1 一起提交，保证每次都是同一个人。
 */
export default function ToolModelBar({ models, disabled }: Props) {
  const text = useGenerationText()
  const [pickerOpen, setPickerOpen] = useState(false)
  const { selected } = models

  // 移出模特库前确认（不删除图片本身）
  function remove() {
    if (!selected) return
    if (!window.confirm(text(`把「${selected.name || '该模特'}」移出模特库？图片仍保留在资产库。`, 'Remove this model from the library? The image stays in your assets.'))) return
    void models.removeModel(selected.id)
  }

  return <div className="gen-model-bar">
    <label className="gen-model-label"><UserRound size={14} />{text('模特', 'Model')}
      <select value={models.selectedId ?? ''} disabled={disabled || !models.loaded}
        onChange={event => models.select(event.target.value ? Number(event.target.value) : null)}>
        <option value="">{text('不使用模特', 'No model')}</option>
        {models.models.map(item => <option key={item.id} value={item.id}>
          {(item.name || text(`模特 #${item.id}`, `Model #${item.id}`)) + (item.is_default ? text('（默认）', ' (default)') : '')}
        </option>)}
      </select>
    </label>
    <div className="gen-model-actions">
      {selected && !selected.is_default && <button type="button" disabled={disabled} onClick={() => void models.setDefault(selected.id)}>
        <Star size={12} />{text('设为默认', 'Set default')}</button>}
      {selected?.is_default && <span className="gen-model-default"><Star size={12} />{text('默认模特', 'Default')}</span>}
      <button type="button" disabled={disabled} onClick={() => setPickerOpen(true)}><UserRoundPlus size={12} />{text('加入模特', 'Add model')}</button>
      {selected && <button type="button" disabled={disabled} onClick={remove}>{text('移出', 'Remove')}</button>}
    </div>
    {models.loaded && !models.models.length && <p className="gen-ref-hint">{text('从资产库选一张满意的模特图加入，之后每次生成都会以她为原型。', 'Add a model photo from your library to keep the same person in every image.')}</p>}
    {models.error && <p className="gen-inline-error" role="alert">{models.error}</p>}
    <ToolReferenceAssetPicker open={pickerOpen} onClose={() => setPickerOpen(false)} allowedKinds={['image']}
      onPick={items => models.addFromAssets(items.map(item => item.id))} />
  </div>
}
