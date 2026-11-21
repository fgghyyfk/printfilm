/** 判断角色本体是否为非人形（会说话的纸鹤、动物、器物等），与 backend drama/character_form.py 对齐。 */

// 描述开头的非人量词（「一位/一个」不算，人物也常用）
const NON_HUMAN_LEAD_RE =
  /^(?:外观[：:]\s*)?(?:是)?一(?:只|头|匹|条|尾|封|台|把|盏|棵|株|朵|枚|颗|架|辆|艘|本|张|片|团|缕|座|块|面|支|根|顶|柄)/
// 「由…折成/制成的」这类器物来历
const CRAFTED_RE = /(?:折|制|做|雕|编|捏|缝|铸|刻|拼)成的/
// 开头一句里出现即视为人物
const HUMAN_MARKER_RE =
  /人|少女|少年|男|女|孩|岁|爷|奶|婆|叔|姨|姐|妹|哥|弟|先生|小姐|青年|老者|士兵|武士|侠|僧|道士|王子|公主|仙子/
// 描述里明确要求人形
const HUMANOID_REQUEST_RE = /化为人形|化成人形|人形形态|拟人化人形|人类外貌|人类模样/
// 开头一句的结束符
const FIRST_CLAUSE_RE = /[，,。；;！!？?\n]/

/**
 * 角色描述（不含结构前缀的正文）是否为非人形本体。
 * 只看开头一句，保守判定；明确要求人形时仍按人物处理。
 */
export function isNonHumanCharacter(description: string | null | undefined): boolean {
  const text = (description || '').trim()
  if (!text || HUMANOID_REQUEST_RE.test(text)) return false
  const first = text.split(FIRST_CLAUSE_RE, 1)[0]
  if (HUMAN_MARKER_RE.test(first)) return false
  return NON_HUMAN_LEAD_RE.test(first) || CRAFTED_RE.test(first)
}
