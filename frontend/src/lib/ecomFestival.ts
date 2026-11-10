/** 电商图节日主题：日期徽标文案等纯函数 */

import type { EcomFestival } from '../api/ecomScenes'

type Text = (zh: string, en: string) => string

/** 节日徽标：进行中 / N 天后 / 几月几日 */
export function festivalBadge(f: EcomFestival, text: Text): string {
  if (f.active) return f.days > 0 ? text(`进行中 · ${f.days} 天后`, `On now · ${f.days}d`) : text('进行中', 'On now')
  if (f.days <= 60) return text(`${f.days} 天后`, `In ${f.days}d`)
  const [, m, d] = f.date.split('-').map(Number)
  return text(`${m}月${d}日`, `${m}/${d}`)
}
