import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useSkillStore } from '../src/stores/skills'

describe('skill store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads skills from the list envelope', async () => {
    const item = { id: 'skill-1', name: 'Recherche', description: 'Sucht Quellen', instructions: 'Prüfe Quellen.' }
    api.get.mockResolvedValue({ data: { items: [item] } })
    const store = useSkillStore()

    await store.fetchSkills()

    expect(api.get).toHaveBeenCalledWith('/skills')
    expect(store.items).toEqual([item])
    expect(store.loading).toBe(false)
  })

  it('creates, updates, and deletes skills with the exact fields', async () => {
    const payload = { name: 'Recherche', description: 'Sucht Quellen', instructions: 'Prüfe Quellen.' }
    const created = { id: 'skill-1', ...payload }
    const updated = { ...created, description: 'Prüft Quellen' }
    api.post.mockResolvedValue({ data: created })
    api.put.mockResolvedValue({ data: updated })
    api.delete.mockResolvedValue({})
    const store = useSkillStore()

    await store.createSkill(payload)
    await store.updateSkill(created.id, { ...payload, description: updated.description })
    await store.deleteSkill(created.id)

    expect(api.post).toHaveBeenCalledWith('/skills', payload)
    expect(api.put).toHaveBeenCalledWith('/skills/skill-1', { ...payload, description: updated.description })
    expect(api.delete).toHaveBeenCalledWith('/skills/skill-1')
    expect(store.items).toEqual([])
  })
})
