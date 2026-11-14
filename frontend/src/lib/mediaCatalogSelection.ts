import type { MediaModelsCatalog } from '../api.ts'

/** Unknown data preserves a draft; a successful empty catalog clears unavailable choices. */
export function resolveCatalogModelSelection(
  catalog: MediaModelsCatalog | null,
  capability: 'image' | 'video',
  currentId: string,
): string {
  if (!catalog) return currentId
  const models = (capability === 'image' ? catalog.image_models : catalog.video_models)
    .filter(model => model.available !== false)
  if (models.some(model => model.id === currentId)) return currentId
  const defaultId = capability === 'image' ? catalog.defaults.image_model : catalog.defaults.video_model
  return models.find(model => model.id === defaultId)?.id || models[0]?.id || ''
}
