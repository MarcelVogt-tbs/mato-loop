import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useUsersStore } from '../src/stores/users'

describe('users store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads and mutates local user state only after successful API calls', async () => {
    api.get.mockResolvedValue({ data: { items: [{ id: '2', username: 'zwei', isActive: true }] } })
    api.post.mockResolvedValue({ data: { id: '1', username: 'eins', isActive: true } })
    api.patch.mockResolvedValue({ data: { id: '2', username: 'zwei', isActive: false } })
    api.put.mockResolvedValue({ data: null })
    api.delete.mockResolvedValue({ data: null })
    const users = useUsersStore()

    await users.fetchAll()
    await users.create({ username: 'eins', password: 'langes-passwort', is_admin: false })
    await users.update('2', { is_active: false })
    await users.resetPassword('2', 'anderes-passwort')
    await users.remove('1')

    expect(users.items).toEqual([{ id: '2', username: 'zwei', isActive: false }])
    expect(api.get).toHaveBeenCalledWith('/users')
    expect(api.post).toHaveBeenCalledWith('/users', {
      username: 'eins', password: 'langes-passwort', is_admin: false
    })
    expect(api.patch).toHaveBeenCalledWith('/users/2', { is_active: false })
    expect(api.put).toHaveBeenCalledWith('/users/2/password', { password: 'anderes-passwort' })
    expect(api.delete).toHaveBeenCalledWith('/users/1')
  })

  it('retains the existing list when an update fails', async () => {
    api.patch.mockRejectedValue(new Error('offline'))
    const users = useUsersStore()
    users.items = [{ id: '1', username: 'eins', isAdmin: false }]

    await expect(users.update('1', { is_admin: true })).rejects.toThrow('offline')

    expect(users.items[0].isAdmin).toBe(false)
  })
})
