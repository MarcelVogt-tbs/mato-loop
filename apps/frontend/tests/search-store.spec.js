import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useSearchStore } from '../src/stores/search'

const response = {
  projects: [{ id: 'project-1', name: 'MatoLoop', sessionCount: 2 }],
  sessions: [{ id: 'session-1', title: 'Suche', matchType: 'title' }],
  strategy: 'hybrid'
}

describe('search store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads grouped global search results', async () => {
    api.get.mockResolvedValue({ data: response })
    const search = useSearchStore()
    search.query = '  mato  '

    await search.run()

    expect(api.get).toHaveBeenCalledWith('/search', { params: { q: 'mato' } })
    expect(search.projects).toEqual(response.projects)
    expect(search.sessions).toEqual(response.sessions)
    expect(search.strategy).toBe('hybrid')
    expect(search.searchedQuery).toBe('mato')
    expect(search.loading).toBe(false)
  })

  it('ignores stale search responses', async () => {
    let resolveFirst
    api.get
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce({ data: response })
    const search = useSearchStore()
    search.query = 'erste Suche'
    const first = search.run()
    search.query = 'zweite Suche'

    await search.run()
    resolveFirst({ data: { projects: [], sessions: [], strategy: 'lexical' } })
    await first

    expect(search.searchedQuery).toBe('zweite Suche')
    expect(search.projects).toEqual(response.projects)
    expect(search.strategy).toBe('hybrid')
  })

  it('keeps retry state after a failed search', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    const search = useSearchStore()
    search.query = 'MatoLoop'

    await expect(search.run()).rejects.toThrow('offline')

    expect(search.loadFailed).toBe(true)
    expect(search.loading).toBe(false)
  })
})
