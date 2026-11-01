// @vitest-environment jsdom
import { createApp, h, ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useProjectReorder } from '../src/features/projects/useProjectReorder'

let app, root, drag, projects

function send(type, target, y) {
  const event = type.startsWith('touch')
    ? Object.assign(new Event(type, { bubbles: true, cancelable: true }), {
      touches: type === 'touchend' ? [] : [{ clientX: 20, clientY: y }]
    })
    : new MouseEvent(type, { bubbles: true, cancelable: true, clientX: 20, clientY: y })
  target.dispatchEvent(event)
  return event
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('requestAnimationFrame', vi.fn())
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  projects = { items: [{ id: 'a' }, { id: 'b' }], reorder: vi.fn().mockResolvedValue() }
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    setup() {
      drag = useProjectReorder(projects, ref(null))
      return () => h('div', {
        onMousedown: drag.startProjectDrag, onTouchstart: drag.startProjectDrag
      }, projects.items.map(({ id }) => h('div', { class: 'project-list-item', 'data-project-id': id }, [
        h('div', { class: 'project-list-item__header' }, id)
      ])))
    }
  })
  app.mount(root)
  document.elementFromPoint = vi.fn((x, y) => (
    root.querySelectorAll('.project-list-item__header')[y < 40 ? 0 : 1]
  ))
})

afterEach(() => {
  app.unmount()
  root.remove()
  delete document.elementFromPoint
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it.each(['mouse', 'touch'])('reorders by %s drag without toggling the project', async (input) => {
  const header = root.querySelector('.project-list-item__header')
  send(input === 'mouse' ? 'mousedown' : 'touchstart', header, 20)
  if (input === 'touch') await vi.advanceTimersByTimeAsync(400)
  send(input === 'mouse' ? 'mousemove' : 'touchmove', document, 60)
  const target = drag.projectDropTarget.value
  send(input === 'mouse' ? 'mouseup' : 'touchend', document, 60)
  const click = send('click', header, 60)

  expect([target, projects.reorder.mock.calls, click.defaultPrevented]).toEqual([
    { id: 'b', after: true }, [[['b', 'a']]], true
  ])
})

it('leaves normal touch scrolling alone before the hold delay', async () => {
  send('touchstart', root.querySelector('.project-list-item__header'), 20)
  const move = send('touchmove', document, 60)
  await vi.advanceTimersByTimeAsync(400)
  send('touchend', document, 60)

  expect([move.defaultPrevented, drag.draggedProject.value, projects.reorder.mock.calls]).toEqual([false, null, []])
})

it('cancels when the source project disappears before the first mouse movement', () => {
  send('mousedown', root.querySelector('.project-list-item__header'), 20)
  projects.items.shift()
  send('mousemove', document, 60)
  send('mousemove', document, 70)
  send('mouseup', document, 70)

  expect(requestAnimationFrame).not.toHaveBeenCalled()
})
