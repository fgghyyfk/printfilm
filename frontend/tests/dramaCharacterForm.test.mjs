import test from 'node:test'
import assert from 'node:assert/strict'
import { loadSource } from './bundleModule.mjs'

const CRANE = '一只由泛黄信纸折成的纸鹤，约巴掌大小，在雨中会发出淡金色的微光。'

test('非人角色走非人设定板，旧人物前缀被修正', async () => {
  const m = await loadSource('../src/lib/dramaStructurePrompt.ts')
  assert.ok(m.ensureStructureInPrompt(CRANE, 'character').startsWith(m.NON_HUMAN_CHARACTER_PROMPT_PREFIX))
  assert.ok(m.ensureStructureInPrompt('十六七岁的少女', 'character').startsWith(m.CHARACTER_PROMPT_PREFIX))
  const old = `${m.CHARACTER_PROMPT_PREFIX}${CRANE}`
  assert.equal(m.ensureStructureInPrompt(old, 'character'), `${m.NON_HUMAN_CHARACTER_PROMPT_PREFIX}${CRANE}`)
  const girl = `${m.CHARACTER_PROMPT_PREFIX}十六七岁的少女`
  assert.equal(m.ensureStructureInPrompt(girl, 'character'), girl)
  assert.equal(m.ensureStructureInPrompt(old, 'prop'), old)
})

test('判定与后端一致', async () => {
  const { isNonHumanCharacter } = await loadSource('../src/lib/dramaCharacterForm.ts')
  const cases = [
    [CRANE, true], ['一头银灰色的老狼，左耳缺了一角', true], ['由竹篾编成的灯笼精灵，会发光', true],
    ['十六七岁的少女，圆润的脸庞', false], ['一位白发老人，拄着拐杖', false], ['一只橘猫少女，头顶猫耳', false],
    ['一只狐狸，修炼千年后化为人形', false], ['盛唐顶流，白衣佩剑', false], ['', false],
  ]
  for (const [text, expected] of cases) assert.equal(isNonHumanCharacter(text), expected, text)
})
