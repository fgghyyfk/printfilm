/** 电商图场景偏好：按品类记住上次选的行业与场景（localStorage，读写失败静默忽略） */

const STORAGE_KEY = 'pf.ecom.scenePrefs'

/** 某个品类上次的选择；sceneKey 为空表示「不指定场景」 */
export type EcomScenePref = { industry: string; sceneKey: string; sceneName: string }

// 读取全部品类的偏好
function readAll(): Record<string, EcomScenePref> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' ? parsed as Record<string, EcomScenePref> : {}
  } catch {
    return {}
  }
}

/** 读取某品类上次的场景偏好，没有返回 null */
export function loadScenePref(category: string): EcomScenePref | null {
  const pref = readAll()[category]
  if (!pref || typeof pref.industry !== 'string') return null
  return { industry: pref.industry, sceneKey: String(pref.sceneKey || ''), sceneName: String(pref.sceneName || '') }
}

/** 保存某品类的场景偏好 */
export function saveScenePref(category: string, pref: EcomScenePref): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...readAll(), [category]: pref }))
  } catch {
    // 隐私模式等场景写不进去，忽略
  }
}

/** 品类默认打开的行业：上次选过的 > 品类映射 > 第一个行业 */
export function defaultSceneIndustry(
  category: string,
  industries: { id: string }[],
  categoryIndustry: Record<string, string> | undefined,
): string {
  const ids = industries.map((i) => i.id)
  const saved = loadScenePref(category)?.industry
  if (saved && ids.includes(saved)) return saved
  const mapped = categoryIndustry?.[category]
  if (mapped && ids.includes(mapped)) return mapped
  return ids[0] || 'daily'
}
