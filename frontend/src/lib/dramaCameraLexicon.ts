import { translate as translateUi } from '../i18n/runtime'
/**
 * 漫剧运镜 / 景别词库（对齐 docs/EPISODE_RULES.md §5）
 * 供分集编辑 @ 菜单插入画面行前缀或运镜短语
 */

export type DramaCameraLexiconGroup = 'shot' | 'move'

export type DramaCameraLexiconItem = {
  id: string
  group: DramaCameraLexiconGroup
  /** 列表展示名 */
  label: string
  /** 插入到脚本的文本（用户可继续补描写） */
  insert: string
  /** 一行说明 */
  hint: string
}

/** 景别标签：写入画面行，勿标成对白 */
export const DRAMA_SHOT_SIZE_LEXICON: DramaCameraLexiconItem[] = [
  { id: 'empty', group: 'shot', get label() { return translateUi("workflow.cutaway") }, insert: '空镜：', get hint() { return translateUi("workflow.establishTheSettingWithoutDialogueOrNarration") } },
  { id: 'wide', group: 'shot', get label() { return translateUi("workflow.wideShot") }, insert: '远景：', get hint() { return translateUi("workflow.showSpatialRelationships") } },
  { id: 'full', group: 'shot', get label() { return translateUi("workflow.fullShot") }, insert: '全景：', get hint() { return translateUi("workflow.frameTheFullBodyAndSettingTogether") } },
  { id: 'medium', group: 'shot', get label() { return translateUi("workflow.mediumShot") }, insert: '中景：', get hint() { return translateUi("workflow.waistUpFramingOftenUsedForDialogue") } },
  { id: 'close', group: 'shot', get label() { return translateUi("workflow.closeShot") }, insert: '近景：', get hint() { return translateUi("workflow.chestUpFramingForAnIntimateViewOfEmotion") } },
  { id: 'closeup', group: 'shot', get label() { return translateUi("workflow.closeUp") }, insert: '特写：', get hint() { return translateUi("workflow.aFaceOrKeyProp") } },
  { id: 'ecu', group: 'shot', get label() { return translateUi("workflow.extremeCloseUp") }, insert: '大特写：', get hint() { return translateUi("workflow.eyesHandsOrSmallDetails") } },
  { id: 'establish', group: 'shot', get label() { return translateUi("workflow.establishingShot") }, insert: '建立镜头：', get hint() { return translateUi("workflow.establishTheSceneAndMoodAtTheOpening") } },
  { id: 'atmosphere', group: 'shot', get label() { return translateUi("workflow.atmosphereShot") }, insert: '气氛镜头：', get hint() { return translateUi("workflow.setTheMoodThroughLightWeatherOrObjects") } },
]

/** 运镜短语：单段运动轴建议 ≤ 2 */
export const DRAMA_CAMERA_MOVE_LEXICON: DramaCameraLexiconItem[] = [
  { id: 'push', group: 'move', get label() { return translateUi("workflow.pushIn") }, insert: '推镜：', get hint() { return translateUi("workflow.moveTheCameraTowardTheSubject") } },
  { id: 'pull', group: 'move', get label() { return translateUi("workflow.pullOut") }, insert: '拉镜：', get hint() { return translateUi("workflow.moveTheCameraBackToRevealTheSetting") } },
  { id: 'pan', group: 'move', get label() { return translateUi("workflow.panTilt") }, insert: '摇镜：', get hint() { return translateUi("workflow.keepTheCameraPositionFixedAndLookHorizontallyOr") } },
  { id: 'truck', group: 'move', get label() { return translateUi("workflow.trackingShot") }, insert: '移镜：', get hint() { return translateUi("workflow.moveTheCameraLaterallyToFollow") } },
  { id: 'follow', group: 'move', get label() { return translateUi("workflow.followShot") }, insert: '跟拍：', get hint() { return translateUi("workflow.followTheCharacterSMovement") } },
  { id: 'high', group: 'move', get label() { return translateUi("workflow.highAngle") }, insert: '俯拍：', get hint() { return translateUi("workflow.lookDownFromAHighAngle") } },
  { id: 'low', group: 'move', get label() { return translateUi("workflow.lowAngle") }, insert: '仰拍：', get hint() { return translateUi("workflow.lookUpFromALowAngle") } },
  { id: 'aerial', group: 'move', get label() { return translateUi("workflow.aerialShot") }, insert: '航拍：', get hint() { return translateUi("workflow.aBroadViewFromAbove") } },
]

/** 合并词库（插入列表用） */
export const DRAMA_CAMERA_LEXICON: DramaCameraLexiconItem[] = [
  ...DRAMA_SHOT_SIZE_LEXICON,
  ...DRAMA_CAMERA_MOVE_LEXICON,
]

/** 运镜使用提示（只展示，不插入） */
export const DRAMA_CAMERA_USAGE_TIPS = [
  '公式：主体 + 动作 + 场景 +（景别/运镜）+（光影）',
  '每段运动轴 ≤ 2（推+摇可以；推+摇+升易失控）',
  '近景大旋转易崩脸，环绕留给中景以上',
  '空镜/景别必须用画面写法，禁止标成对白或旁白',
] as const

// 按关键字过滤词库（匹配 label / insert / hint）
export function filterDramaCameraLexicon(
  items: DramaCameraLexiconItem[],
  query: string,
): DramaCameraLexiconItem[] {
  const q = (query || '').trim().toLowerCase()
  if (!q) return items
  return items.filter(
    (item) =>
      item.label.toLowerCase().includes(q) ||
      item.insert.toLowerCase().includes(q) ||
      item.hint.toLowerCase().includes(q),
  )
}
