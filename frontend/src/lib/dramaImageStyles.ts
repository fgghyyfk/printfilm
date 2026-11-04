import { translate as translateUi } from '../i18n/runtime'
/** Drama image style options (aligned with manju imageStyles). */

export const IMAGE_STYLE_IDS = [
  'retro-sci-fi-atompunk',
  'palace-intrigue-cold',
  'domestic-suspense-cold',
  'ancient-romance-soft',
  'ancient-chinese-mythology',
  'japanese-youth-film',
  'japanese-daily-natural',
  'korean-urban-soft',
  'chinese-urban-realistic',
  'wuxia-realistic-photo',
  '90s-realistic-film',
  'retro-narrative-film',
  'american-retro-hollywood',
  'neon-cyberpunk-film',
  '90s-rural-china-film',
  'cgi-3d-animation',
  'ghibli-handdrawn-anime',
  'tezuka-era-cartoon',
  'shanghai-animation',
  'pixel-art',
  'shadow-puppet-illustration',
] as const

export type ImageStyleId = (typeof IMAGE_STYLE_IDS)[number]

export const IMAGE_STYLE_OPTIONS: Array<{ id: ImageStyleId; label: string }> = [
  { id: 'retro-sci-fi-atompunk', get label() { return translateUi("workflow.retroSciFiAtompunk") } },
  { id: 'palace-intrigue-cold', get label() { return translateUi("workflow.coldPalaceIntrigue") } },
  { id: 'domestic-suspense-cold', get label() { return translateUi("workflow.coolChineseSuspense") } },
  { id: 'ancient-romance-soft', get label() { return translateUi("workflow.softLightPeriodRomance") } },
  { id: 'ancient-chinese-mythology', get label() { return translateUi("workflow.epicChineseMythology") } },
  { id: 'japanese-youth-film', get label() { return translateUi("workflow.japaneseYouthFilm") } },
  { id: 'japanese-daily-natural', get label() { return translateUi("workflow.naturalJapaneseDailyLife") } },
  { id: 'korean-urban-soft', get label() { return translateUi("workflow.softLightKoreanUrbanDrama") } },
  { id: 'chinese-urban-realistic', get label() { return translateUi("workflow.realisticChineseUrbanDrama") } },
  { id: 'wuxia-realistic-photo', get label() { return translateUi("workflow.realisticWuxiaPhotography") } },
  { id: '90s-realistic-film', get label() { return translateUi("workflow.text1990sRealistCinema") } },
  { id: 'retro-narrative-film', get label() { return translateUi("workflow.retroNarrativeCinema") } },
  { id: 'american-retro-hollywood', get label() { return translateUi("workflow.classicHollywood") } },
  { id: 'neon-cyberpunk-film', get label() { return translateUi("workflow.neonCyberpunkCinema") } },
  { id: '90s-rural-china-film', get label() { return translateUi("workflow.text1990sRuralChineseCinema") } },
  { id: 'cgi-3d-animation', get label() { return translateUi("workflow.text3dAnimation") } },
  { id: 'ghibli-handdrawn-anime', get label() { return translateUi("workflow.miyazakiInspiredHandDrawnArt") } },
  { id: 'tezuka-era-cartoon', get label() { return translateUi("workflow.tezukaEraCartoonArt") } },
  { id: 'shanghai-animation', get label() { return translateUi("workflow.shanghaiAnimationStyle") } },
  { id: 'pixel-art', get label() { return translateUi("workflow.pixelArt") } },
  { id: 'shadow-puppet-illustration', get label() { return translateUi("workflow.shadowPuppetIllustration") } },
]

export const EPISODE_COUNT_PRESETS = [1, 12, 24, 36, 48] as const

export function getImageStyleLabel(styleId: string | undefined | null): string | null {
  if (!styleId) return null
  return IMAGE_STYLE_OPTIONS.find((o) => o.id === styleId)?.label ?? null
}
