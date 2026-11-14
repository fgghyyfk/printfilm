import { useEffect, useState } from 'react'
import { listToolModels, updateToolModel, type ToolModel } from '../api/tools'

/**
 * 图片生成页的模特库：加载本人模特、默认选中默认模特，可切换、设默认、加入/移出。
 * selectedId 为 null 表示本次不使用模特。
 */
export function useToolModels(enabled: boolean) {
  /* 模特列表与本次所选 */
  const [models, setModels] = useState<ToolModel[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')

  // 首次加载：有默认模特就预选它
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    void listToolModels(controller.signal)
      .then(result => {
        setModels(result.items)
        setSelectedId(result.items.find(item => item.is_default)?.id ?? null)
        setLoaded(true)
      })
      .catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '加载模特库失败') })
    return () => controller.abort()
  }, [enabled])

  /** 保存后用服务端返回的列表刷新；所选模特被移出时改为不使用 */
  async function save(assetId: number, body: { is_model: boolean; is_default?: boolean }) {
    setError('')
    try {
      const result = await updateToolModel(assetId, body)
      setModels(result.items)
      setSelectedId(current => (current && result.items.some(item => item.id === current) ? current : null))
      return result.items
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存模特失败')
      return null
    }
  }

  /** 把资产库选中的图片加入模特库（库里还没有默认时，第一张设为默认），并选中第一张 */
  async function addFromAssets(assetIds: number[]) {
    let hasDefault = models.some(item => item.is_default)
    for (const id of assetIds) {
      const items = await save(id, { is_model: true, is_default: !hasDefault })
      if (!items) return
      hasDefault = true
    }
    if (assetIds[0]) setSelectedId(assetIds[0])
  }

  const selected = models.find(item => item.id === selectedId) ?? null
  return {
    models, selected, selectedId, loaded, error, select: setSelectedId, addFromAssets,
    setDefault: (id: number) => save(id, { is_model: true, is_default: true }),
    removeModel: (id: number) => save(id, { is_model: false }),
  }
}
