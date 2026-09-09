// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QSelect, QSeparator, QSpinner, QTab, QTabs, QToggle, QToolbar, QToolbarTitle } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
const mocks = vi.hoisted(() => ({ stores: {}, confirm: vi.fn(async () => true) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: mocks.confirm }) }))
vi.mock('stores/automations', () => ({ useAutomationStore: () => mocks.stores.automations }))
vi.mock('stores/channels', () => ({ useChannelStore: () => mocks.stores.channels }))
vi.mock('stores/chat', () => ({ useChatStore: () => mocks.stores.chat }))
vi.mock('stores/skills', () => ({ useSkillStore: () => mocks.stores.skills }))
vi.mock('stores/execution-agents', () => ({ useExecutionAgentStore: () => mocks.stores.executionAgents }))

import Editor from '../src/pages/settings/AutomationEditorPage.vue'
import { scheduleDraft } from '../src/utils/automation-schedules'

let app, root
const definition = (overrides = {}) => ({
  id: 'automation', revision: 4, name: 'Tagesbericht', task: 'Berichte über den Tag.', enabled: true,
  archivedAt: null, schedules: [], runtime: { modelId: 'model', memoryMode: 'full' },
  effectiveState: { enabled: false, activeRunStatus: null, nextRunAt: null }, ...overrides
})
const run = (id = 'run-1', overrides = {}) => ({
  id, automationId: 'automation', automationRevision: 4, status: 'completed', trigger: 'manual',
  createdAt: '2026-09-09T10:15:27Z', completedAt: '2026-09-09T10:16:27Z', result: { content: `Ergebnis ${id}` },
  definition: { task: 'Historischer Auftrag' }, delivery: { status: 'not_requested' }, ...overrides
})
const summary = (run) => ({
  id: run.id, automationId: run.automationId, automationRevision: run.automationRevision,
  status: run.status, trigger: run.trigger, createdAt: run.createdAt, completedAt: run.completedAt, delivery: run.delivery
})

async function mountEditor({ path = '/settings/automations/automation', automation = definition(), loadError = null } = {}) {
  let saved = automation
  const store = reactive({
    runs: [], page: 0, hasMore: false, status: null,
    fetchAutomation: vi.fn(async () => { if (loadError) throw loadError; return structuredClone(saved) }),
    fetchAutomationStatus: vi.fn(async () => ({ id: saved.id, revision: saved.revision, enabled: saved.enabled, archivedAt: saved.archivedAt, scheduleCount: saved.schedules.length, effectiveState: saved.effectiveState })),
    createAutomation: vi.fn(async (payload) => { saved = definition({ ...payload, revision: 1 }); return structuredClone(saved) }),
    updateAutomation: vi.fn(async (_id, payload) => { saved = { ...saved, ...payload, revision: saved.revision + 1 }; return structuredClone(saved) }),
    runNow: vi.fn(async () => run('new-run', { status: 'queued' })),
    retryDelivery: vi.fn(async (_id, runId) => run(runId, { delivery: { status: 'pending' } })),
    fetchRuns: vi.fn(async () => { store.runs = [summary(run())]; store.page = 1; return { items: store.runs, page: 1, hasMore: false } }),
    fetchRun: vi.fn(async (_id, runId) => run(runId)),
    clearDetail: vi.fn(() => { store.runs = []; store.page = 0; store.hasMore = false })
  })
  mocks.stores = {
    automations: store,
    channels: reactive({ providers: [], endpoints: [], fetchAll: vi.fn(async () => {}) }),
    chat: reactive({ sessionCatalog: [{ id: 'session', title: 'Meine Session' }], fetchSessionCatalog: vi.fn(async () => {}) }),
    skills: reactive({ items: [], fetchSkills: vi.fn(async () => {}) }),
    executionAgents: { byId: () => null, fetchAll: vi.fn(async () => {}) }
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/settings/automations/new', name: 'settings-automation-create', component: Editor },
    { path: '/settings/automations/:automationId', name: 'settings-automation-detail', component: Editor },
    { path: '/settings/automations', name: 'settings-automations', component: { render: () => null } }
  ] })
  await router.push(path)
  root = document.createElement('div')
  document.body.append(root)
  let page
  app = createApp({ render: () => h(RouterView, null, { default: ({ Component }) => h(Component, { ref: (value) => { if (value) page = value.$.setupState } }) }) })
  app.use(router).use(Quasar, { components: { QBanner, QBtn, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QSelect, QSeparator, QSpinner, QTab, QTabs, QToggle, QToolbar, QToolbarTitle } })
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(page?.savedDraft || page?.loadFailed).toBeTruthy())
  await nextTick()
  return { page, router, store }
}

afterEach(() => {
  app?.unmount()
  app = null
  root?.remove()
  vi.restoreAllMocks()
  mocks.confirm.mockClear()
})

it('creates a manual automation and saves directly to the canonical route without a discard guard', async () => {
  const { page, router, store } = await mountEditor({ path: '/settings/automations/new' })
  expect(page.form.manual).toBe(true)
  expect(page.form.skillId).toBeNull()
  page.form.name = 'Neue Automation'
  page.form.task = 'Prüfe die Aufgabe.'
  await page.saveAutomation()
  expect(store.createAutomation).toHaveBeenCalledWith(expect.objectContaining({ schedules: [], skillId: null, archiveAfterSuccess: false }))
  expect(router.currentRoute.value.name).toBe('settings-automation-detail')
  expect(router.currentRoute.value.path).toBe('/settings/automations/automation')
  expect(mocks.confirm).not.toHaveBeenCalled()
  expect(page.saving).toBe(false)
  expect(page.dirty).toBe(false)
  expect(store.fetchAutomation).not.toHaveBeenCalled()
  expect(root.querySelectorAll('[role="tab"]')).toHaveLength(2)
  expect(root.querySelector('form').hasAttribute('inert')).toBe(false)
})

it('preserves dirty inputs on tab and exact-run query navigation, and never implicitly saves on run now', async () => {
  const { page, router, store } = await mountEditor()
  page.form.task = 'Ungespeicherter Auftrag'
  page.tab = 'history'
  await nextTick()
  await router.replace({ query: { run: 'notification-run' } })
  await vi.waitFor(() => expect(store.fetchRun).toHaveBeenCalledWith('automation', 'notification-run', { suppressErrorToast: true }))
  expect(page.form.task).toBe('Ungespeicherter Auftrag')
  expect(page.editingAutomation.revision).toBe(4)
  expect(page.tab).toBe('history')
  expect(mocks.confirm).not.toHaveBeenCalled()
  expect(store.fetchAutomation).toHaveBeenCalledTimes(1)
  expect(root.textContent).toContain('Ergebnis notification-run')
  page.tab = 'settings'
  await nextTick()
  expect(page.form.task).toBe('Ungespeicherter Auftrag')
  await page.runNow()
  expect(store.runNow).not.toHaveBeenCalled()
  expect(store.updateAutomation).not.toHaveBeenCalled()
  expect(root.textContent).toContain('bitte Änderungen speichern')
  await router.push({ name: 'settings-automations' })
  expect(mocks.confirm).toHaveBeenCalledTimes(1)
})

it('blocks scheduled submission for invalid collapsed advanced fields, then preserves multiple schedules', async () => {
  const { page, store } = await mountEditor()
  page.setExecutionMode('cron')
  page.form.schedules[0].timezone = 'Invalid/Zone'
  page.tab = 'history'
  await page.saveAutomation()
  expect(page.tab).toBe('settings')
  expect(page.form.schedules[0].advanced).toBe(true)
  expect(store.updateAutomation).not.toHaveBeenCalled()
  expect(root.textContent).toContain('gültige IANA-Zeitzone')
  page.form.schedules[0].timezone = 'UTC'
  page.form.schedules[0].validUntil = '2026-02-30T10:00'
  await page.saveAutomation()
  expect(store.updateAutomation).not.toHaveBeenCalled()
  page.form.schedules[0].validUntil = ''
  page.form.schedules.push(scheduleDraft({ runAt: '2027-02-03T11:12:43.123456+01:00', enabled: false }))
  await page.saveAutomation()
  expect(store.updateAutomation).toHaveBeenCalledWith('automation', expect.objectContaining({
    expectedRevision: 4,
    schedules: [
      { cron: '0 8 * * *', timezone: 'UTC', validUntil: null, enabled: true },
      { runAt: '2027-02-03T11:12:43.123456+01:00', enabled: false }
    ]
  }))
})

it('retains schedule drafts while switching to manual but saves an empty list and no archive flag', async () => {
  const { page, store } = await mountEditor({ automation: definition({ archiveAfterSuccess: true, schedules: [{ runAt: '2027-02-03T11:12:43Z', enabled: false }] }) })
  const draft = JSON.stringify(page.form.schedules)
  page.setExecutionMode('manual')
  expect(JSON.stringify(page.form.schedules)).toBe(draft)
  page.setExecutionMode('once')
  expect(JSON.stringify(page.form.schedules)).toBe(draft)
  page.setExecutionMode('manual')
  await page.saveAutomation()
  expect(store.updateAutomation).toHaveBeenCalledWith('automation', expect.objectContaining({ schedules: [], archiveAfterSuccess: false }))
})

it('keeps archived settings read-only while exact-run history and delivery retry stay interactive', async () => {
  const { page, store } = await mountEditor({ path: '/settings/automations/automation?run=archived-run', automation: definition({ archivedAt: '2026-09-01T10:00:00Z' }) })
  await vi.waitFor(() => expect(root.textContent).toContain('Ergebnis archived-run'))
  expect(page.tab).toBe('history')
  expect(root.querySelector('form').hasAttribute('inert')).toBe(false)
  expect(root.querySelector('input[readonly]')).not.toBeNull()
  expect(root.querySelector('textarea').readOnly).toBe(true)
  expect(root.querySelector('#automation-history-panel').hasAttribute('inert')).toBe(false)
  expect(root.textContent.indexOf('Ergebnis archived-run')).toBeLessThan(root.textContent.indexOf('Konfiguration dieses Laufs'))
  expect(root.textContent).not.toContain('Nicht angefordert')
  store.fetchRuns.mockImplementationOnce(async () => {
    store.runs = [summary(run('archived-run', { delivery: { status: 'failed' } }))]
    return { items: store.runs, page: 1, hasMore: false }
  })
  store.fetchRun.mockResolvedValueOnce(run('archived-run', { delivery: { status: 'failed' } }))
  page.tab = 'settings'
  await nextTick()
  page.tab = 'history'
  await vi.waitFor(() => expect(root.textContent).toContain('Zustellung erneut versuchen'))
  const retry = [...root.querySelectorAll('button')].find((button) => button.textContent.includes('Zustellung erneut versuchen'))
  retry.click()
  await vi.waitFor(() => expect(store.retryDelivery).toHaveBeenCalledWith('automation', 'archived-run'))
  await page.saveAutomation()
  await page.runNow()
  expect(store.updateAutomation).not.toHaveBeenCalled()
  expect(store.runNow).not.toHaveBeenCalled()
})

it('polls status without reloading the definition or editing revision, and reloads conflicts only after confirmation', async () => {
  const { page, store } = await mountEditor()
  page.form.name = 'Mein Entwurf'
  store.fetchAutomationStatus.mockResolvedValue({ id: 'automation', revision: 5, enabled: true, archivedAt: null, scheduleCount: 0, effectiveState: { enabled: false, activeRunStatus: 'running' } })
  await page.refreshStatus()
  expect(page.conflict).toBe(true)
  expect(page.form.name).toBe('Mein Entwurf')
  expect(page.editingAutomation.revision).toBe(4)
  expect(store.fetchAutomation).toHaveBeenCalledTimes(1)
  expect(store.fetchRuns).not.toHaveBeenCalled()
  expect(store.fetchRun).not.toHaveBeenCalled()
  await page.saveAutomation()
  expect(store.updateAutomation).not.toHaveBeenCalled()
  mocks.confirm.mockResolvedValueOnce(false)
  await page.reloadDefinition()
  expect(page.form.name).toBe('Mein Entwurf')
  expect(store.fetchAutomation).toHaveBeenCalledTimes(1)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  await page.refreshStatus()
  expect(store.fetchAutomationStatus).toHaveBeenCalledTimes(1)
})

it('preserves unavailable delivery bindings and clears stale live status after saving', async () => {
  const { page, store } = await mountEditor({ automation: definition({ outputSessionId: 'session', outputSessionRequired: true, deliveryChannel: { type: 'endpoint', endpointId: 'missing-channel' } }) })
  await page.refreshStatus()
  expect(page.liveStatus).not.toBeNull()
  expect(page.deliveryOptions).toContainEqual(expect.objectContaining({ value: 'missing-channel', disable: true }))
  page.form.name = 'Neuer Name'
  await page.saveAutomation()
  expect(store.updateAutomation).toHaveBeenCalledWith('automation', expect.objectContaining({ deliveryChannelType: 'endpoint', deliveryEndpointId: 'missing-channel', outputSessionRequired: true }))
  expect(page.liveStatus).toBeNull()
  expect(page.editingAutomation.revision).toBe(5)
  expect(page.conflict).toBe(false)
})

it('ignores a stale status response after saving and an old definition response after changing id', async () => {
  const { page, router, store } = await mountEditor()
  let resolveStatus
  store.fetchAutomationStatus.mockImplementationOnce(() => new Promise((resolve) => { resolveStatus = resolve }))
  const polling = page.refreshStatus()
  page.form.name = 'Gespeicherter Name'
  await page.saveAutomation()
  resolveStatus({ id: 'automation', revision: 4 })
  await polling
  expect(page.liveStatus).toBeNull()
  expect(page.editingAutomation.revision).toBe(5)
  let resolveOld
  store.fetchAutomation.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
  const reloading = page.reloadDefinition()
  await nextTick()
  store.fetchAutomation.mockResolvedValueOnce(definition({ id: 'other', name: 'Andere Automation' }))
  await router.push('/settings/automations/other')
  await vi.waitFor(() => expect(page.editingAutomation?.id).toBe('other'))
  resolveOld(definition())
  await reloading
  expect(page.editingAutomation.id).toBe('other')
})

it('shows scheduling controls only for scheduled mode and keeps pause primary', async () => {
  const { page } = await mountEditor()
  const execution = root.querySelector('[aria-labelledby="automation-execution-heading"]')
  expect(execution.textContent).not.toContain('Weitere Ausführungseinstellungen')
  expect(execution.textContent).not.toContain('Zeitpläne aktiv')
  page.setExecutionMode('once')
  page.form.enabled = false
  page.form.archiveAfterSuccess = true
  await nextTick()
  expect(execution.textContent).toContain('Zeitpläne pausiert.')
  const toggle = [...execution.querySelectorAll('[role="switch"]')].find((element) => element.textContent.includes('Zeitpläne aktiv'))
  expect(toggle).toBeTruthy()
  expect(toggle.closest('[style*="display: none"]')).toBeNull()
  expect(execution.textContent).toContain('Nach erfolgreichem geplanten Termin archivieren')
  expect(page.executionOptionsSummary).toContain('nach erfolgreichem geplanten Termin')
  page.setExecutionMode('manual')
  await nextTick()
  expect(execution.textContent).not.toContain('Weitere Ausführungseinstellungen')
  expect(execution.textContent).not.toContain('Zeitpläne aktiv')
})

it('explains automatic skill selection, durable context, chat binding, and the default model accurately', async () => {
  const { page } = await mountEditor({ automation: definition({ runtime: { modelId: null } }) })
  expect(page.runtimeItems.find((item) => item.label === 'Modell').value).toBe('Standardmodell')
  const context = [...root.querySelectorAll('p')].find((element) => element.textContent.includes('Frühere Ergebnisse und Dateien'))
  expect(context.closest('[style*="display: none"]')).toBeNull()
  expect(root.textContent).toContain('Skill verbindlich verwenden (optional)')
  expect(root.textContent).toContain('Skills automatisch auswählen')
  expect(root.textContent).not.toContain('Ohne Skill gilt allein deine Aufgabe')
  page.form.outputSessionId = 'session'
  page.form.outputSessionRequired = true
  await nextTick()
  expect(root.textContent).toContain('Automation an diesen Chat binden')
  expect(root.textContent).toContain('Beim Löschen dieses Chats muss auch die Automation gelöscht werden.')
  expect(page.outputOptionsSummary).toContain('An diesen Chat gebunden')
  page.assignForm(definition({ runtime: { modelId: '00000000-0000-4000-8000-000000000001' } }))
  await nextTick()
  expect(page.runtimeItems.find((item) => item.label === 'Modell').value).toBe('Gespeichertes Modell')
  const technical = [...root.querySelectorAll('.runtime-value')].find((element) => element.textContent.includes('00000000-0000-4000-8000-000000000001'))
  expect(technical.closest('[style*="display: none"]')).not.toBeNull()
})

async function pollHistory() {
  document.dispatchEvent(new Event('visibilitychange'))
  await nextTick()
  await vi.waitFor(() => expect(root.querySelector('#automation-history-panel [aria-busy]').getAttribute('aria-busy')).toBe('false'))
}

it('polls summaries without re-fetching stable completed results, and discovers a new newest run', async () => {
  const { page, store } = await mountEditor()
  page.tab = 'history'
  await vi.waitFor(() => expect(root.textContent).toContain('Ergebnis run-1'))
  expect(store.fetchRun).toHaveBeenCalledTimes(1)
  const revision = [...root.querySelectorAll('.text-caption')].find((element) => element.textContent === 'Revision 4')
  expect(revision.closest('[style*="display: none"]')).not.toBeNull()
  await pollHistory()
  expect(store.fetchRuns).toHaveBeenCalledTimes(2)
  expect(store.fetchRun).toHaveBeenCalledTimes(1)
  store.fetchRuns.mockImplementationOnce(async () => {
    store.runs = [summary(run('newest')), summary(run())]
    return { items: store.runs, page: 1, hasMore: false }
  })
  await pollHistory()
  expect(store.fetchRun).toHaveBeenLastCalledWith('automation', 'newest', { suppressErrorToast: true })
  expect(root.textContent).toContain('Ergebnis newest')
  page.tab = 'settings'
  await nextTick()
  const count = store.fetchRuns.mock.calls.length
  await pollHistory()
  expect(store.fetchRuns).toHaveBeenCalledTimes(count)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  page.tab = 'history'
  await nextTick()
  await pollHistory()
  expect(store.fetchRuns).toHaveBeenCalledTimes(count)
})

it('refreshes active runs, pending delivery, and changed summary fields, but not unchanged final results', async () => {
  const { page, store } = await mountEditor()
  let current = run('run-1', { status: 'running', completedAt: null, result: null })
  store.fetchRuns.mockImplementation(async () => {
    store.runs = [summary(current)]
    return { items: store.runs, page: 1, hasMore: false }
  })
  store.fetchRun.mockImplementation(async () => structuredClone(current))
  page.tab = 'history'
  await vi.waitFor(() => expect(root.textContent).toContain('Das Ergebnis erscheint nach Abschluss'))
  await pollHistory()
  expect(store.fetchRun).toHaveBeenCalledTimes(2)
  current = run('run-1', { delivery: { status: 'pending', attempts: 0, nextAttemptAt: null } })
  await pollHistory()
  await pollHistory()
  expect(store.fetchRun).toHaveBeenCalledTimes(4)
  current = run('run-1', { delivery: { status: 'delivered', attempts: 1, nextAttemptAt: null } })
  await pollHistory()
  expect(store.fetchRun).toHaveBeenCalledTimes(5)
  await pollHistory()
  expect(store.fetchRun).toHaveBeenCalledTimes(5)
  current = { ...current, completedAt: '2026-09-09T10:17:27Z' }
  await pollHistory()
  expect(store.fetchRun).toHaveBeenCalledTimes(6)
  current = { ...current, delivery: { ...current.delivery, attempts: 2 } }
  await pollHistory()
  expect(store.fetchRun).toHaveBeenCalledTimes(7)
})

it('removing the run query selects the newest run without discarding the form', async () => {
  const { page, router, store } = await mountEditor({ path: '/settings/automations/automation?run=older' })
  await vi.waitFor(() => expect(root.textContent).toContain('Ergebnis older'))
  page.form.name = 'Ungespeicherter Name'
  await router.replace({ query: {} })
  await vi.waitFor(() => expect(root.textContent).toContain('Ergebnis run-1'))
  expect(root.textContent).not.toContain('Ergebnis older')
  expect(page.form.name).toBe('Ungespeicherter Name')
  expect(store.fetchAutomation).toHaveBeenCalledTimes(1)
  expect(mocks.confirm).not.toHaveBeenCalled()
})

it('does not show an empty history or create hint when the automation fails to load', async () => {
  const { page, store } = await mountEditor({ path: '/settings/automations/automation?run=older', loadError: new Error('load failed') })
  expect(page.loadFailed).toBe(true)
  expect(root.textContent).toContain('Die Automation konnte nicht geladen werden.')
  expect(root.textContent).not.toContain('Noch keine Läufe')
  expect(root.textContent).not.toContain('Nach dem Speichern')
  expect(root.textContent).not.toContain('Noch nicht gespeichert')
  expect(store.fetchRuns).not.toHaveBeenCalled()
})

it('shows a history error instead of an empty state after summary loading fails', async () => {
  const { page, store } = await mountEditor()
  store.fetchRuns.mockRejectedValueOnce(new Error('load failed'))
  page.tab = 'history'
  await vi.waitFor(() => expect(root.textContent).toContain('Der Verlauf oder der ausgewählte Lauf konnte nicht geladen werden.'))
  expect(root.textContent).not.toContain('Noch keine Läufe')
  expect(store.fetchRun).not.toHaveBeenCalled()
})
