import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SHORT_VIDEO_CREATE_PATH,
  SHORT_VIDEO_LIST_PATH,
  shortVideoCreatePath,
  shortVideoProjectPath,
  legacyStudioEntryPath,
} from '../src/lib/shortVideoRoutes.ts'
import { CREATION_NAV, resolveNavActive } from '../src/lib/siteNavigation.ts'

test('short-video navigation opens project cards while templates start the step workflow', () => {
  assert.equal(SHORT_VIDEO_CREATE_PATH, '/studio/new')
  assert.equal(SHORT_VIDEO_LIST_PATH, '/history')
  assert.equal(CREATION_NAV.find(({ key }) => key === 'kepu').path, SHORT_VIDEO_LIST_PATH)
  assert.equal(shortVideoCreatePath(), SHORT_VIDEO_CREATE_PATH)
  assert.equal(shortVideoCreatePath('anim_3d'), '/studio/new?template=anim_3d')
  assert.equal(shortVideoCreatePath('style & colour'), '/studio/new?template=style%20%26%20colour')
  assert.equal(resolveNavActive(SHORT_VIDEO_CREATE_PATH), 'kepu')
})

test('existing session projects keep their recovery route in every production state', () => {
  for (const status of ['DRAFT', 'SCRIPTING', 'GENERATING', 'COMPOSING', 'DONE', 'FAILED']) {
    assert.equal(
      shortVideoProjectPath({ id: 40, status, creation_session_id: 12 }),
      '/studio/sessions/12?project=40',
    )
  }
  assert.equal(resolveNavActive('/studio/sessions/12'), 'kepu')
})

test('legacy projects retain draft and asset editor entry points', () => {
  assert.equal(shortVideoProjectPath({ id: 40, status: 'DRAFT' }), '/studio/40/style')
  assert.equal(shortVideoProjectPath({ id: 41, status: 'DRAFT', pipeline_mode: 'image_text' }), '/studio/new?project=41')
  assert.equal(shortVideoProjectPath({ id: 40, status: 'DONE', creation_session_id: null }), '/studio/40')
  assert.equal(resolveNavActive('/history'), 'kepu')
})

test('historical conversation versions retain their own project while opening the step editor', () => {
  assert.equal(shortVideoProjectPath({ id: 41, status: 'DONE', creation_session_id: 12 }), '/studio/sessions/12?project=41')
  assert.equal(shortVideoProjectPath({ id: 42, status: 'DONE', creation_session_id: 12 }), '/studio/sessions/12?project=42')
})

test('old studio bookmarks open cards while explicit creation links retain their parameters', () => {
  assert.equal(legacyStudioEntryPath({}), '/history')
  assert.equal(legacyStudioEntryPath({ project: '40' }), '/studio/40')
  assert.equal(legacyStudioEntryPath({ project: 'not-a-project' }), '/history')
  assert.equal(legacyStudioEntryPath({ template: 'ink_guofeng' }), '/studio/new?template=ink_guofeng')
  assert.equal(legacyStudioEntryPath({ mode: 'image_text' }), '/studio/new?mode=image_text')
})
