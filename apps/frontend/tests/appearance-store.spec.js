// @vitest-environment jsdom
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const { api, notifyError } = vi.hoisted(() => ({ api: { get: vi.fn(), put: vi.fn(), delete: vi.fn() }, notifyError: vi.fn() }))
vi.mock('src/boot/axios', () => ({ api }))
vi.mock('src/utils/notifications', () => ({ notifyError }))
import { applyBranding, applyTheme, useAppearanceStore } from '../src/features/settings/appearance-store'

beforeEach(() => {
  setActivePinia(createPinia())
  document.head.innerHTML = '<meta name="description"><meta name="apple-mobile-web-app-title"><link rel="icon"><link rel="apple-touch-icon"><link id="appearance-theme" rel="stylesheet" href="/themes/matoloop/theme.css">'
})
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers() })

it('replaces styles only after the next theme loads, keeping one active stylesheet', async () => {
  const previous = document.getElementById('appearance-theme')
  const request = applyTheme('/themes/example/theme.css')
  const next = document.head.lastElementChild
  expect(previous.isConnected).toBe(true)
  expect(next.media).toBe('not all')
  next.dispatchEvent(new Event('load'))
  await request
  expect(document.querySelectorAll('#appearance-theme')).toHaveLength(1)
  expect(previous.isConnected).toBe(false)
  expect(next.media).toBe('all')
})

it('keeps the current theme after a stylesheet load failure', async () => {
  const previous = document.getElementById('appearance-theme')
  const request = applyTheme('/themes/missing/theme.css')
  document.head.lastElementChild.dispatchEvent(new Event('error'))
  await expect(request).rejects.toMatchObject({ error: { code: 'appearance.themeLoadFailed' } })
  expect(document.getElementById('appearance-theme')).toBe(previous)
})

it('uses branding for title and icons without losing the unread count', () => {
  applyBranding({ appName: 'Teamchat', description: 'Unser Team', logoUrl: '/api/appearance/logo?v=123', customLogo: true }, 4)
  expect(document.title).toBe('(4) Teamchat')
  expect(document.querySelector('link[rel="icon"]').getAttribute('href')).toBe('/api/appearance/logo?v=123')
  expect(document.querySelector('meta[name="description"]').content).toBe('Unser Team')
})

it('does not replace confirmed server settings after a failed save', async () => {
  const store = useAppearanceStore()
  api.put.mockRejectedValue(new Error('offline'))
  await expect(store.saveThemes({ defaultThemeId: 'example', disabledThemeIds: [] })).rejects.toThrow('offline')
  expect(store.defaultThemeId).toBe('matoloop')
  expect(store.saving).toBe(false)
})
