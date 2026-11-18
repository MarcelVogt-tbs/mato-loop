// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QCard, QCardActions, QCardSection, QDialog, QIcon, QSelect, QSeparator, QSpinner } from 'quasar'
import ChangelogDialog from '../src/features/changelog/ChangelogDialog.vue'
import { useChangelogStore } from '../src/features/changelog/store'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('src/boot/axios', () => ({ api }))
let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks(); api.get.mockReset() })

async function mount(comparison = null) {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useChangelogStore()
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(ChangelogDialog) })
  app.use(pinia).use(Quasar, { components: { QBanner, QBtn, QCard, QCardActions, QCardSection, QDialog, QIcon, QSelect, QSeparator, QSpinner } })
  app.mount(root)
  await store.show(comparison)
  await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull())
  return store
}

const response = (extra = {}) => ({ data: {
  base_revision: 'a'.repeat(40), target_revision: 'b'.repeat(40),
  base_version: '2026-11-17-aaaaaaaa', target_version: null,
  state: 'behind', status: 'ok', incomplete: false, compare_url: null,
  notes: [{ category: 'Fixed', text: '<script>external text</script>' }], ...extra
} })

it('switches remote channel and renders external notes as text', async () => {
  api.get.mockResolvedValue(response())
  const store = await mount()
  expect(api.get).toHaveBeenLastCalledWith('/changelog', { params: { channel: 'stable' } })
  store.channel = 'develop'
  await store.fetchChanges()
  expect(api.get).toHaveBeenLastCalledWith('/changelog', { params: { channel: 'develop' } })
  expect(document.querySelector('li').textContent).toBe('<script>external text</script>')
  expect(document.querySelector('li script')).toBeNull()
})

it('uses exact PWA revisions without a remote selector', async () => {
  api.get.mockResolvedValue(response())
  await mount({ base: 'a'.repeat(40), target: 'b'.repeat(40) })
  expect([api.get.mock.calls[0][1].params, document.querySelector('[role="combobox"]')]).toEqual([
    { base_revision: 'a'.repeat(40), target_revision: 'b'.repeat(40) }, null
  ])
})

it.each([
  [{ state: 'identical' }, 'Beide Stände sind identisch.'],
  [{ state: 'diverged' }, 'Die Stände sind auseinanderentwickelt.'],
  [{ status: 'unknown_build' }, 'Ein genauer Vergleich ist nicht möglich.'],
  [{ status: 'unavailable' }, 'Der Vergleich ist momentan nicht verfügbar.'],
  [{ incomplete: true }, 'Die Änderungsliste ist unvollständig.']
])('explains comparison limits: %j', async (extra, text) => {
  api.get.mockResolvedValue(response(extra))
  await mount()
  await vi.waitFor(() => expect(document.body.textContent).toContain(text))
})
