// @vitest-environment jsdom
import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { Quasar, QBadge, QBtn, QIcon, QItem, QItemSection, QList, QMenu, QSeparator, QTab, QTabs, QTooltip, ClosePopup, Ripple, Screen } from 'quasar'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }))
vi.mock('src/control-center/apps', async () => {
  const { h } = await import('vue')
  const { controlCenterIntegrationManifest, controlCenterGlobalSectionManifest } = await import('src/control-center/manifest')
  const withViews = (items) => items.map((item) => ({
    ...item,
    component: { render: () => h('div', { 'data-app': item.id }, item.label) }
  }))
  const controlCenterIntegrations = withViews(controlCenterIntegrationManifest)
  const controlCenterGlobalSections = withViews(controlCenterGlobalSectionManifest)
  return {
    controlCenterIntegrations,
    controlCenterGlobalSections,
    controlCenterContentsById: new Map([...controlCenterIntegrations, ...controlCenterGlobalSections].map((item) => [item.id, item]))
  }
})

import ControlCenterPanel from '../src/components/ControlCenterPanel.vue'
import { useControlCenterStore } from '../src/stores/control-center'
import { useApprovalStore } from '../src/stores/approvals'
import { useQuestionStore } from '../src/stores/questions'
import { useInAppNotificationStore } from '../src/stores/in-app-notifications'
import { api } from 'src/boot/axios'

const components = { QBadge, QBtn, QIcon, QItem, QItemSection, QList, QMenu, QSeparator, QTab, QTabs, QTooltip }
const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')
let app, root, pinia, controlCenter

beforeEach(() => {
  vi.resetAllMocks()
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  vi.stubGlobal('innerWidth', 1440)
  pinia = createPinia()
  setActivePinia(pinia)
  controlCenter = useControlCenterStore()
  controlCenter.resetDraft()
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  if (originalScrollIntoView) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScrollIntoView)
  else delete HTMLElement.prototype.scrollIntoView
  vi.unstubAllGlobals()
  expect(api.get).not.toHaveBeenCalled()
  expect(api.post).not.toHaveBeenCalled()
  expect(api.patch).not.toHaveBeenCalled()
})

async function mountPanel() {
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(ControlCenterPanel)
  app.use(pinia).use(Quasar, { components, directives: { ClosePopup, Ripple } })
  app.mount(root)
  window.dispatchEvent(new Event('resize'))
  await nextTick()
}

function notificationButton() {
  return root.querySelector('[aria-label^="Benachrichtigungen öffnen"]')
}

describe('control center panel', () => {
  it('combines approvals, questions and unread hints and retains the pending badge at zero unread', async () => {
    const approvals = useApprovalStore()
    const questions = useQuestionStore()
    const notifications = useInAppNotificationStore()
    approvals.pending = [{ id: 'approval' }]
    questions.pending = [{ id: 'question' }]
    notifications.unreadCount = 3
    await mountPanel()

    const button = notificationButton()
    expect(button.querySelector('.q-badge').textContent).toBe('5')
    expect(button.getAttribute('aria-label')).toBe('Benachrichtigungen öffnen, 2 offene Aufgaben, 3 ungelesene Hinweise')

    notifications.unreadCount = 0
    await nextTick()
    expect(button.querySelector('.q-badge').textContent).toBe('2')
    expect(button.getAttribute('aria-label')).toBe('Benachrichtigungen öffnen, 2 offene Aufgaben, 0 ungelesene Hinweise')
    expect(approvals.pending).toHaveLength(1)
    expect(questions.pending).toHaveLength(1)

    approvals.pending = []
    questions.pending = []
    await nextTick()
    expect(button.querySelector('.q-badge')).toBeNull()
  })

  it.each(['files', 'computer-files', 'file-viewer'])('keeps %s mounted but hidden while global notifications are shown', async (appId) => {
    if (appId === 'file-viewer') {
      await controlCenter.openFile({ scope: 'session', rootIdentity: 'draft', path: 'notes.md' })
    } else {
      await controlCenter.openApp(appId)
    }
    const activeTabId = controlCenter.activeAppId
    await mountPanel()
    const content = root.querySelector(`[data-app="${appId}"]`)
    const notifications = root.querySelector('[data-app="notifications"]')
    expect(content).not.toBeNull()
    expect(content.style.display).not.toBe('none')
    expect(notifications.style.display).toBe('none')

    notificationButton().click()
    await nextTick()
    expect(controlCenter.activeGlobalSectionId).toBe('notifications')
    expect(controlCenter.activeAppId).toBe(activeTabId)
    expect(root.querySelector(`[data-app="${appId}"]`)).toBe(content)
    expect(content.style.display).toBe('none')
    expect(notifications.style.display).not.toBe('none')

    root.querySelector(`[id="control-center-tab-${activeTabId}"]`).click()
    await nextTick()
    expect(controlCenter.activeGlobalSectionId).toBeNull()
    expect(controlCenter.activeContentId).toBe(activeTabId)
    expect(root.querySelector(`[data-app="${appId}"]`)).toBe(content)
    expect(content.style.display).not.toBe('none')
    expect(notifications.style.display).toBe('none')
  })

  it('uses a dense size-20 panel close button without col-auto and closes the draft', async () => {
    await controlCenter.setOpen(true)
    await mountPanel()
    const close = root.querySelector('[aria-label="Control Center schließen"]')
    expect(close.classList.contains('q-btn--dense')).toBe(true)
    expect(close.style.fontSize).toBe('20px')
    expect(close.classList.contains('col-auto')).toBe(false)
    close.click()
    await nextTick()
    expect(controlCenter.open).toBe(false)
  })

  it('navigates nested tabs with ArrowRight, Home and End without consuming close-button keys', async () => {
    await controlCenter.openApp('agent-coding')
    await controlCenter.openApp('mail')
    await mountPanel()
    const tabs = [...root.querySelectorAll('.control-center__tab')]
    expect(tabs).toHaveLength(3)
    tabs[0].focus()

    for (const [key, index] of [['ArrowRight', 1], ['End', 2], ['Home', 0]]) {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
      document.activeElement.dispatchEvent(event)
      await nextTick()
      expect(event.defaultPrevented).toBe(true)
      expect(document.activeElement).toBe(tabs[index])
    }

    const close = tabs[0].parentElement.querySelector('.control-center__tab-close')
    close.focus()
    for (const key of ['ArrowRight', 'Home', 'End']) {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
      close.dispatchEvent(event)
      await nextTick()
      expect(event.defaultPrevented).toBe(false)
      expect(document.activeElement).toBe(close)
    }
    expect(controlCenter.tabs).toHaveLength(4)
  })

  it('activates a tab through the real desktop overflow menu', async () => {
    await controlCenter.openApp('agent-coding')
    await controlCenter.openApp('notifications')
    await mountPanel()
    await vi.waitFor(() => expect(Screen.gt.sm).toBe(true))

    // jsdom can trigger this menu; its CSS visibility is checked in a real browser.
    root.querySelector('[aria-label="Geöffnete Tabs anzeigen"]').click()
    await vi.waitFor(() => expect(document.querySelector('.q-menu')).not.toBeNull())
    const item = [...document.querySelectorAll('.q-menu .q-item')].find((item) => item.textContent.includes('Ablage'))
    expect(item).toBeDefined()
    item.click()
    await nextTick()
    expect(controlCenter.activeAppId).toBe('files')
    expect(controlCenter.activeGlobalSectionId).toBeNull()
    expect(root.querySelector('#control-center-tab-files').getAttribute('aria-selected')).toBe('true')
  })
})
