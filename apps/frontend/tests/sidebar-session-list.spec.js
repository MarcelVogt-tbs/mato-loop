// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QItem, QItemLabel, QItemSection, QList } from 'quasar'
import SidebarSessionList from '../src/features/chat/components/SidebarSessionList.vue'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('../src/shared/ui/SidebarNavItem.vue', () => ({
  default: { props: ['label'], render() { return h('button', { class: 'sidebar-nav-item' }, this.label) } }
}))
vi.mock('../src/features/chat/components/SidebarSessionItem.vue', () => ({
  default: {
    props: ['session'],
    emits: ['drag-start', 'drag-end'],
    render() {
      return h('div', {
        class: 'session-list-item', tabindex: 0, draggable: true,
        'data-session': this.session.id,
        onDragstart: (event) => this.$emit('drag-start', event),
        onDragend: () => this.$emit('drag-end')
      }, [this.session.title, h('button', 'Aktionen')])
    }
  }
}))

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks() })

it('bounds loaded session DOM on the outer scroll area while retaining the native drag source', async () => {
  const items = Array.from({ length: 2000 }, (_, index) => ({ id: `${index}`, title: `Session ${index}` }))
  items[0].pinned = true
  root = document.createElement('div')
  root.id = 'app-navigation-drawer'
  root.innerHTML = '<div class="session-scroll-area"><div class="scroll"><div id="session-test"></div></div></div>'
  document.body.append(root)
  const target = root.querySelector('.scroll')
  Object.defineProperties(target, { clientHeight: { value: 300 }, scrollHeight: { value: 60060 } })
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(30)
  const dragEnd = vi.fn()
  app = createApp({ render: () => h(SidebarSessionList, {
    list: { loaded: true, items, nextCursor: 'more' }, showGroups: true, onDragEnd: dragEnd
  }) })
  app.use(Quasar, { components: { QItem, QItemLabel, QItemSection, QList } })
  app.component('QResizeObserver', { render: () => null })
  app.mount(root.querySelector('#session-test'))

  await vi.waitUntil(() => root.querySelector('[data-session="0"]'))
  vi.spyOn(root.querySelector('.q-virtual-scroll'), 'getBoundingClientRect').mockImplementation(() => ({
    top: -target.scrollTop, bottom: 60060 - target.scrollTop, height: 60060, left: 0, right: 288, width: 288
  }))
  const source = root.querySelector('[data-session="0"]')
  const focusTarget = source.querySelector('button')
  focusTarget.focus()
  source.dispatchEvent(new Event('dragstart', { bubbles: true }))
  await nextTick()
  target.scrollTop = 15000
  target.dispatchEvent(new Event('scroll'))
  await vi.waitUntil(() => root.querySelector('[data-session="500"]'))
  const retainedSource = root.querySelector('[data-session="0"]')
  const retained = retainedSource === source && source.classList.contains('q-virtual-scroll--skip')
  const restoredFocus = document.activeElement === focusTarget
  const pagination = root.querySelector('.sidebar-nav-item')
  pagination.focus()
  source.dispatchEvent(new Event('dragend', { bubbles: true }))
  await nextTick()

  expect({
    bounded: root.querySelectorAll('.session-list-item').length < 100,
    outerScrollOnly: root.querySelectorAll('.scroll').length === 1,
    retained,
    restoredFocus,
    outsideFocus: document.activeElement === pagination,
    dragEnded: dragEnd.mock.calls.length,
    released: root.querySelector('[data-session="0"]') === null,
    pagination: pagination.textContent
  }).toEqual({ bounded: true, outerScrollOnly: true, retained: true, restoredFocus: true,
    outsideFocus: true, dragEnded: 1, released: true, pagination: 'Mehr anzeigen' })
})
