import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn() }
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

  it('manages the selected scope and enabled state', async () => {
    const payload = { name: 'Recherche', description: 'Sucht Quellen', instructions: 'Prüfe Quellen.' }
    const created = { id: 'skill-1', ...payload }
    const updated = { ...created, description: 'Prüft Quellen' }
    api.post.mockResolvedValue({ data: created })
    api.put.mockResolvedValue({ data: updated })
    api.delete.mockResolvedValue({})
    api.patch.mockResolvedValue({ data: { ...updated, enabled: false } })
    const store = useSkillStore()
    store.selectScope('instance')

    await store.createSkill(payload)
    await store.updateSkill(created.id, { ...payload, description: updated.description })
    await store.setEnabled(created.id, false)
    await store.deleteSkill(created.id)

    const config = { params: { scope: 'instance' } }
    expect(api.post).toHaveBeenCalledWith('/skills', payload, config)
    expect(api.put).toHaveBeenCalledWith('/skills/skill-1', { ...payload, description: updated.description }, config)
    expect(api.patch).toHaveBeenCalledWith('/skills/skill-1/enabled', { enabled: false }, config)
    expect(api.delete).toHaveBeenCalledWith('/skills/skill-1', config)
    expect(store.scopedItems).toEqual([])
  })

  it('loads a scoped catalog without replacing effective automation choices', async () => {
    const effective = { id: 'effective' }
    const personal = { id: 'personal' }
    api.get
      .mockResolvedValueOnce({ data: { items: [effective] } })
      .mockResolvedValueOnce({ data: { items: [personal] } })
    const store = useSkillStore()

    await store.fetchSkills()
    await store.fetchSkills('personal')

    expect(api.get).toHaveBeenLastCalledWith('/skills', { params: { scope: 'personal' } })
    expect(store.items).toEqual([effective])
    expect(store.scopedItems).toEqual([personal])
  })
})
