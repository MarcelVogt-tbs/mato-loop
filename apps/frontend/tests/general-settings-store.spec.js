import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({ api: { get: vi.fn(), put: vi.fn() } }))
vi.mock('src/boot/axios', () => ({ api }))
import { useGeneralSettingsStore } from '../src/features/settings/general-settings-store'

it('loads the effective address and keeps server state after a failed save', async () => {
  setActivePinia(createPinia())
  const current = { publicBaseUrl: null, effectivePublicBaseUrl: 'https://mato.example.test', publicUrlSource: 'automatic' }
  api.get.mockResolvedValue({ data: current })
  api.put.mockResolvedValueOnce({ data: { ...current, publicBaseUrl: 'https://public.example.test', effectivePublicBaseUrl: 'https://public.example.test', publicUrlSource: 'settings' } }).mockRejectedValueOnce(new Error('failed'))
  const store = useGeneralSettingsStore()
  await store.fetchSettings()
  await store.saveSettings('https://public.example.test')
  await expect(store.saveSettings('https://other.example.test')).rejects.toThrow()
  expect(store.publicBaseUrl).toBe('https://public.example.test')
})
