/** 品牌与站点固定信息 */

export const BRAND_NAME = '开口播'
export const BRAND_NAME_EN = 'KaiKou'

/** 按界面语言取品牌短名：英文界面不出现中文名 */
export function brandName(locale: string): string {
  return locale === 'en' ? BRAND_NAME_EN : BRAND_NAME
}

/** 按界面语言取品牌全称（版权行、无障碍名称）：中文界面保留中英双名 */
export function brandFullName(locale: string): string {
  return locale === 'en' ? BRAND_NAME_EN : `${BRAND_NAME} ${BRAND_NAME_EN}`
}
export const SITE_ORIGIN = 'https://kaikou.haituike.com'
export const SUPPORT_EMAIL = 'support@haituike.com'
