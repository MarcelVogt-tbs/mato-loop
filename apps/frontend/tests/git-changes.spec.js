// @vitest-environment jsdom
import { createApp, h, nextTick, shallowReactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import FileChanges from '../src/features/chat/components/FileChanges.vue'
import { gitContext, useGitChangesStore } from '../src/features/files/git-store'

const { api } = vi.hoisted(() => ({ api: { get: vi.fn() } }))
vi.mock('src/boot/axios', () => ({ api }))
const context = { sessionId: 'session', executionAgentId: 'agent', currentDirectory: '/repo' }
let app
beforeEach(() => { setActivePinia(createPinia()); api.get.mockReset() })
afterEach(() => app?.unmount())

it('keeps the fallback and shows deleted or uncounted Git files', async () => {
  const root = document.createElement('div')
  const onOpenFile = vi.fn(), onOpenDiff = vi.fn()
  const actions = [{ root: 'workspace', executionAgentId: 'agent', path: '/repo/code.py', filename: 'code.py', kind: 'file', operation: 'edit' }]
  const props = shallowReactive({ actions, gitFiles: null })
  app = createApp({ render: () => h(FileChanges, { ...props, onOpenFile, onOpenDiff }) })
  app.use(createPinia())
  for (const name of ['QList', 'QItemSection', 'QItemLabel', 'QIcon', 'QTooltip', 'QBtn']) {
    app.component(name, { render() { return h('div', this.$attrs, this.$slots.default?.()) } })
  }
  app.component('QItem', { render() { return h('button', this.$attrs, this.$slots.default?.()) } })
  app.mount(root)
  root.querySelector('button').click()
  expect(onOpenFile).toHaveBeenCalledWith(expect.objectContaining({ path: '/repo/code.py' }))
  expect(root.textContent).not.toContain('Aktueller Git-Stand')
  props.gitFiles = [{ path: 'removed.py', status: 'deleted', additions: 0, deletions: 7 }]
  await nextTick()
  root.querySelector('button').click()
  expect(onOpenDiff).toHaveBeenCalledWith('removed.py')
  expect(root.textContent).toContain('−7')
  props.gitFiles = [{ path: 'large.py', status: 'added', additions: null, deletions: null }]
  await nextTick()
  expect([...root.querySelectorAll('.app-muted .text-positive, .app-muted .text-negative')].map((item) => item.textContent)).toEqual(['+–', '−–'])
})

it('uses the captured repository directory when a diff file parent has been deleted', () => {
  const session = { id: context.sessionId, preferredExecutionAgentId: context.executionAgentId, currentDirectory: '/workspace' }
  const target = { scope: 'filesystem', executionAgentId: context.executionAgentId, path: '/repo/removed/file.py', currentDirectory: '/repo' }
  expect(gitContext(session, target, null, true)).toEqual(context)
})

it('keeps a source context stable after discovering its repository', () => {
  const session = { id: context.sessionId, preferredExecutionAgentId: context.executionAgentId, currentDirectory: '/workspace' }
  const target = { scope: 'filesystem', executionAgentId: context.executionAgentId, path: '/repo/src/file.py', currentDirectory: '/workspace' }
  const initial = gitContext(session, target)
  expect(gitContext(session, target, { ...initial, repository: '/repo' })).toEqual(initial)
})

it('discards an old context response and clears stale counts on failure', async () => {
  const git = useGitChangesStore()
  git.setContext(context)
  let finish
  api.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  const old = git.refresh()
  git.setContext({ ...context, currentDirectory: '/worktree' })
  finish({ data: { repository: '/repo', files: [{ path: 'old.py' }], truncated: false } })
  await old
  expect(git.current).toMatchObject({ repository: null, files: [] })
  api.get.mockResolvedValueOnce({ data: { repository: '/worktree', files: [{ path: 'one.py' }], truncated: false } })
  await git.refresh()
  api.get.mockRejectedValueOnce(new Error('offline'))
  await git.refresh()
  expect(git.current).toMatchObject({ files: [], error: expect.any(Error) })
})

it('keeps the newest refresh when an older list request is still pending', async () => {
  const git = useGitChangesStore()
  git.setContext(context)
  let finish
  api.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  api.get.mockResolvedValueOnce({ data: { repository: '/repo', files: [{ path: 'saved.py' }], truncated: false } })
  const pending = git.refresh()
  const latest = git.refresh()
  finish({ data: { repository: '/repo', files: [], truncated: false } })
  await Promise.all([pending, latest])
  expect(git.current.files).toEqual([{ path: 'saved.py' }])
})

it('ignores a late preview after another file has been requested', async () => {
  const git = useGitChangesStore()
  git.setContext(context)
  let finish
  api.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  const old = git.loadDiff('old.py')
  api.get.mockResolvedValueOnce({ data: { file: { path: 'new.py' } } })
  await git.loadDiff('new.py')
  finish({ data: { file: { path: 'old.py' } } })
  await old
  expect(git.current.preview.file.path).toBe('new.py')
})
