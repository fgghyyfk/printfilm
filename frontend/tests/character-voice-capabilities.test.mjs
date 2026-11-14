import assert from 'node:assert/strict'
import test from 'node:test'
import { characterVoiceGenerationEnabled, requireCharacterVoiceGeneration } from '../src/lib/publicCapabilities.ts'

test('new voice generation requires the explicit server capability, independent of existing voice binding', () => {
  for (const value of [null, undefined, {}, { drama_voice_generation_enabled: false }, { drama_voice_generation_enabled: 'true' }, { drama_voice_binding_enabled: true }]) {
    assert.equal(characterVoiceGenerationEnabled(value), false)
  }
  assert.equal(characterVoiceGenerationEnabled({ drama_voice_generation_enabled: true }), true)
})

test('disabled or unavailable capability rejects before any prompt, synthesis, or binding work', async () => {
  let generated = 0
  for (const load of [async () => ({}), async () => ({ drama_voice_generation_enabled: false }), async () => { throw new Error('network unavailable') }]) {
    await assert.rejects(async () => {
      await requireCharacterVoiceGeneration(load)
      generated += 1
    }, /音色生成暂时停用/)
  }
  assert.equal(generated, 0)
})

test('explicitly enabled capability permits generation without altering unrelated capabilities', async () => {
  await requireCharacterVoiceGeneration(async () => ({ drama_voice_generation_enabled: true, ordinary_tts_enabled: false }))
})
