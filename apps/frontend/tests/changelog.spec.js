// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QCard, QCardActions, QCardSection, QDialog, QIcon, QSelect, QSeparator, QSpinner, QTab, QTabs } from 'quasar'
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
  app.use(pinia).use(Quasar, { components: { QBanner, QBtn, QCard, QCardActions, QCardSection, QDialog, QIcon, QSelect, QSeparator, QSpinner, QTab, QTabs } })
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

it('opens the installed comparison first and switches to develop without mixing notes', async () => {
  api.get.mockResolvedValueOnce(response({ notes: [{ category: 'Fixed', text: 'Already installed' }] }))
    .mockResolvedValue(response())
  const store = await mount()
  const tabs = document.querySelectorAll('[role="tab"]')
  expect(tabs[0].getAttribute('aria-selected')).toBe('true')
  expect(api.get).toHaveBeenCalledExactlyOnceWith('/changelog/installed')
  expect(document.querySelector('[role="combobox"]')).toBeNull()
  expect(document.querySelector('li').textContent).toBe('Already installed')
  tabs[1].click()
  await vi.waitFor(() => expect(document.querySelector('li')?.textContent).toBe('<script>external text</script>'))
  expect(api.get).toHaveBeenLastCalledWith('/changelog', { params: { channel: 'develop' } })
  expect(document.querySelector('[role="combobox"]')).not.toBeNull()
  expect(document.querySelector('li script')).toBeNull()

  store.channel = 'stable'
  api.get.mockResolvedValue(response({ status: 'no_release', notes: [] }))
  await store.fetchChanges()
  expect(api.get).toHaveBeenLastCalledWith('/changelog', { params: { channel: 'stable' } })
  expect(document.body.textContent).toContain('Es gibt noch keine offizielle Veröffentlichung.')
})

it('uses exact PWA revisions without a remote selector', async () => {
  api.get.mockResolvedValue(response())
  await mount({ base: 'a'.repeat(40), target: 'b'.repeat(40) })
  expect([api.get.mock.calls[0][1].params, document.querySelector('[role="combobox"]')]).toEqual([
    { base_revision: 'a'.repeat(40), target_revision: 'b'.repeat(40) }, null
  ])
  expect(document.querySelector('[role="tablist"]')).toBeNull()
})

it.each([
  [{ state: 'identical' }, 'Beide Stände sind identisch.'],
  [{ state: 'diverged' }, 'Die Stände sind auseinanderentwickelt.'],
  [{ status: 'no_previous_build', notes: [] }, 'MatoLoop hat noch keinen vorherigen Installationsstand aufgezeichnet.'],
  [{ status: 'unknown_build' }, 'Ein genauer Vergleich ist nicht möglich.'],
  [{ status: 'unavailable' }, 'Der Vergleich ist momentan nicht verfügbar.'],
  [{ incomplete: true }, 'Die Änderungsliste ist unvollständig.']
])('explains comparison limits: %j', async (extra, text) => {
  api.get.mockResolvedValue(response(extra))
  await mount()
  await vi.waitFor(() => expect(document.body.textContent).toContain(text))
})
