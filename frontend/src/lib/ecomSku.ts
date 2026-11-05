import { validateEcomImages } from './ecomJobs.ts'

/** SKU 必须包含唯一场景和 1–20 张产品图，校验双方格式与大小。 */
export function validateSkuImages(products: readonly Pick<File, 'type' | 'size'>[], scenes: readonly Pick<File, 'type' | 'size'>[]): string | null {
  if (scenes.length !== 1) return 'sceneRequired'
  return validateEcomImages(scenes, 1) || validateEcomImages(products, 20)
}

/** 新增图片默认取文件名；删除图片时按对象保留已有编辑名，支持同名文件。 */
export function skuNamesForFiles<T extends Pick<File, 'name'>>(next: readonly T[], previous: readonly T[] = [], names: readonly string[] = []): string[] {
  return next.map((file, index) => {
    const previousIndex = previous.indexOf(file)
    return previousIndex >= 0 && names[previousIndex] !== undefined
      ? names[previousIndex]
      : (file.name.replace(/\.[^.]+$/, '').trim() || `SKU ${index + 1}`).slice(0, 100)
  })
}
