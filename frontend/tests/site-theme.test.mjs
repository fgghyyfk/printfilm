import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { DEFAULT_SITE_THEME, SITE_THEMES, normalizeSiteTheme, previewThemeFromSearch } from '../src/lib/siteTheme.ts'

test('未知主题回退默认', () => {
  assert.equal(normalizeSiteTheme('studio'), 'studio')
  assert.equal(normalizeSiteTheme('x'), DEFAULT_SITE_THEME)
  assert.equal(normalizeSiteTheme(undefined), DEFAULT_SITE_THEME)
})

test('预览主题只接受白名单', () => {
  assert.equal(previewThemeFromSearch('?preview_theme=studio'), 'studio')
  assert.equal(previewThemeFromSearch('?preview_theme=<script>'), null)
  assert.equal(previewThemeFromSearch(''), null)
})

test('每个主题都有样式文件，首屏脚本白名单一致', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
  for (const theme of SITE_THEMES) {
    assert.ok(html.includes(`'${theme}'`), theme)
    const css = readFileSync(new URL(`../src/styles/themes/${theme}.css`, import.meta.url), 'utf8')
    assert.ok(css.includes(`html[data-theme="${theme}"]`), theme)
  }
})
