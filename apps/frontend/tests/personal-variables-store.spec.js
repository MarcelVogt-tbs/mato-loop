import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))
vi.mock('src/boot/axios', () => ({ api }))

import { usePersonalVariableStore } from '../src/stores/personal-variables'

describe('personal variable store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads and applies CRUD results without exposing or inventing secret values', async () => {
    const secret = { id: 'secret-1', name: 'API_TOKEN', secret: true }
    const variable = { id: 'variable-1', name: 'CITY', value: 'Berlin', secret: false }
    api.get.mockResolvedValue({ data: [secret] })
    api.post.mockResolvedValue({ data: variable })
    api.put.mockResolvedValue({ data: { ...variable, value: 'Hamburg' } })
    api.delete.mockResolvedValue({})
    const store = usePersonalVariableStore()

    await store.fetchVariables()
    await store.createVariable({ name: 'CITY', value: 'Berlin', secret: false })
    await store.updateVariable(variable.id, { name: 'CITY', value: 'Hamburg', secret: false })
    await store.removeVariable(secret.id)

    expect(api.get).toHaveBeenCalledWith('/personal-variables')
    expect(api.post).toHaveBeenCalledWith('/personal-variables', { name: 'CITY', value: 'Berlin', secret: false })
    expect(api.put).toHaveBeenCalledWith('/personal-variables/variable-1', { name: 'CITY', value: 'Hamburg', secret: false })
    expect(api.delete).toHaveBeenCalledWith('/personal-variables/secret-1')
    expect(store.variables).toEqual([{ ...variable, value: 'Hamburg' }])
  })
})
