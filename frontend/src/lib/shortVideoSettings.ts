import type { MediaModelOption, MediaModelsCatalog, PipelineMode } from '../api'

type MediaSettings = {
  imageModel: string
  videoModel: string
  imageResolution: string
  videoResolution: string
  ratio: string
}

const RATIOS = ['9:16', '16:9', '1:1', '4:3', '21:9']

/** New projects use image-to-video; explicit still-image links keep their existing mode. */
export function shortVideoPipelineMode(value?: string | null): PipelineMode {
  return value === 'image_text' ? 'image_text' : 'full'
}

/** Reserve style/subject and continuity slots while accepting legacy catalogs without limits. */
function referenceModels(models: MediaModelOption[], imageReferences: number) {
  return models.filter(model => model.available !== false && (model.reference_limits === undefined || model.reference_limits.images >= imageReferences))
}

/** Only blank selections may use defaults; an unavailable explicit model must be replaced by the user. */
function selectModel(models: MediaModelOption[], current: string, preferred: string) {
  if (current) return models.find(model => model.id === current)
  return models.find(model => model.id === preferred)
    || models[0]
}

/** Priced resolutions take priority over the model's general capability list. */
function resolutions(model?: MediaModelOption) {
  return model?.available_resolutions ?? model?.allowed_resolutions ?? []
}

/** Keep supported quality choices and clamp incompatible choices after a model change. */
function selectResolution(model: MediaModelOption | undefined, current: string) {
  const allowed = resolutions(model)
  return allowed.includes(current) ? current
    : allowed.find(value => value === model?.defaults?.resolution) || allowed[0] || ''
}

/** Both stages use reference images; incompatible model parameters cannot start production. */
export function resolveShortVideoMediaSettings(catalog: MediaModelsCatalog | null, mode: PipelineMode, current: MediaSettings, imageReferenceCount = 2) {
  const imageOptions = referenceModels(catalog?.image_models ?? [], imageReferenceCount)
  const videoOptions = referenceModels(catalog?.video_models ?? [], 2)
  const image = selectModel(imageOptions, current.imageModel, catalog?.defaults.image_model || '')
  const video = selectModel(videoOptions, current.videoModel, catalog?.defaults.video_model || '')
  const imageRatios = image?.allowed_aspect_ratios ?? RATIOS
  const videoRatios = video?.allowed_aspect_ratios ?? RATIOS
  const ratios = RATIOS.filter(ratio => imageRatios.includes(ratio) && (mode === 'image_text' || videoRatios.includes(ratio)))
  const imageResolution = image ? selectResolution(image, current.imageResolution) : current.imageResolution
  const videoResolution = video ? selectResolution(video, current.videoResolution) : current.videoResolution
  const ratio = ratios.includes(current.ratio) ? current.ratio : ratios[0] || ''
  return {
    imageOptions, videoOptions, ratios,
    imageResolutions: resolutions(image), videoResolutions: resolutions(video),
    imageModel: current.imageModel || image?.id || '',
    videoModel: current.videoModel || video?.id || '',
    imageSelectionUnavailable: Boolean(catalog && current.imageModel && !image),
    videoSelectionUnavailable: Boolean(catalog && current.videoModel && !video),
    imageResolution: catalog ? imageResolution : current.imageResolution,
    videoResolution: catalog ? videoResolution : current.videoResolution,
    ratio: catalog ? ratio : current.ratio,
    ready: Boolean(catalog && image && imageResolution && ratio && (mode === 'image_text' || (video && videoResolution))),
  }
}
