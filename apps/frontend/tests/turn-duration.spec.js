// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { Quasar, QIcon, QTooltip } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

import TurnDuration from '../src/features/chat/components/TurnDuration.vue'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.useRealTimers()
})

it('ticks a live turn and keeps a completed duration fixed beside its label', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-12T12:00:05.000Z'))
  const running = ref(true)
  const history = ref({
    activeDurationMs: 5_000,
    activeDurationUpdatedAt: '2026-09-12T12:00:05.000Z'
  })
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    setup: () => () => h('div', { class: 'chat-message__label' }, [
      h('span', 'MatoLoop'),
      h(TurnDuration, { history: history.value, running: running.value })
    ])
  })
  app.use(Quasar, { components: { QIcon, QTooltip } })
  app.mount(root)

  expect(root.querySelectorAll('[role="timer"]')).toHaveLength(1)
  expect(root.querySelector('[role="timer"]').textContent).toContain('5 s')
  await vi.advanceTimersByTimeAsync(1_000)
  expect(root.querySelector('[role="timer"]').textContent).toContain('6 s')

  running.value = false
  history.value = { activeDurationMs: 9_000, activeDurationUpdatedAt: null }
  await nextTick()
  expect(root.querySelector('[role="timer"]').textContent).toContain('9 s')
  await vi.advanceTimersByTimeAsync(2_000)
  expect(root.querySelector('[role="timer"]').textContent).toContain('9 s')
  expect(root.querySelector('[role="timer"]').getAttribute('aria-label'))
    .toBe('Antwortdauer ohne Wartezeit auf dich: 9 s')
})
