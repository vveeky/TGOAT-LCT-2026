import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  APPEARANCE_THEME_STORAGE_KEY,
  CLASSIC_THEME,
  GRAPHITE_THEME,
  nextAppearanceTheme,
  persistAppearanceTheme,
  readAppearanceTheme,
} from '../src/theme.js'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('appearance preference defaults safely and restores only supported themes', () => {
  const values = new Map()
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }

  assert.equal(readAppearanceTheme(storage), CLASSIC_THEME)
  values.set(APPEARANCE_THEME_STORAGE_KEY, GRAPHITE_THEME)
  assert.equal(readAppearanceTheme(storage), GRAPHITE_THEME)
  values.set(APPEARANCE_THEME_STORAGE_KEY, 'unknown')
  assert.equal(readAppearanceTheme(storage), CLASSIC_THEME)
  assert.equal(readAppearanceTheme({ getItem() { throw new Error('blocked') } }), CLASSIC_THEME)
})

test('appearance toggle and persistence stay reversible when storage is unavailable', () => {
  const values = new Map()
  const storage = { setItem: (key, value) => values.set(key, value) }

  assert.equal(nextAppearanceTheme(CLASSIC_THEME), GRAPHITE_THEME)
  assert.equal(nextAppearanceTheme(GRAPHITE_THEME), CLASSIC_THEME)
  persistAppearanceTheme(GRAPHITE_THEME, storage)
  assert.equal(values.get(APPEARANCE_THEME_STORAGE_KEY), GRAPHITE_THEME)
  assert.doesNotThrow(() => persistAppearanceTheme(GRAPHITE_THEME, { setItem() { throw new Error('blocked') } }))
})

test('shared topbar exposes the current theme and a keyboard-accessible toggle on every route', () => {
  const app = source('../src/App.jsx')
  const topbar = source('../src/components/layout/Topbar.jsx')

  assert.match(app, /document\.documentElement\.dataset\.theme = theme/)
  assert.match(app, /persistAppearanceTheme\(theme\)/)
  assert.match(app, /<Topbar onThemeChange=\{setTheme\} theme=\{theme\} \/>/)
  assert.match(topbar, /aria-pressed=\{theme === 'graphite'\}/)
  assert.match(topbar, /onClick=\{\(\) => onThemeChange\(nextAppearanceTheme\(theme\)\)\}/)
  assert.match(topbar, /Сейчас: .*Графит.*Классик/)
})

test('graphite recolors neutral surfaces while preserving orange risk semantics', () => {
  const css = source('../src/index.css')
  const graphiteTokens = css.match(/:root\[data-theme="graphite"\]\s*\{([^}]*)\}/)?.[1]

  assert.ok(graphiteTokens)
  assert.match(graphiteTokens, /--bg:\s*#e7e9ec/i)
  assert.match(graphiteTokens, /--surface:\s*#f7f8f9/i)
  assert.doesNotMatch(graphiteTokens, /--risk-/)
  assert.match(css, /\.theme-toggle svg\s*\{/)
})
