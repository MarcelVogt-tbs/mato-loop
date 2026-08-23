import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))
vi.mock('src/boot/axios', () => ({ api }))

import { useIntegrationStore } from '../src/stores/integrations'

const response = {
  configured: true,
  accounts: [{ id: 'mail-1', name: 'Arbeit', integrationType: 'mail', providerType: 'imap_smtp' }]
}

describe('mail integration settings dispatch', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    api.get.mockResolvedValue({ data: response })
  })

  it('dispatches CRUD and connection testing through the selected scope', async () => {
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    api.put.mockResolvedValue({})
    api.delete.mockResolvedValue({})
    const store = useIntegrationStore()
    store.selectScope('personal')
    const payload = { name: 'Arbeit', password: 'secret', imap_host: 'imap.example.com' }

    await store.fetchMail()
    await store.createMail(payload)
    await store.updateMail('mail-1', { ...payload, password: '' })
    await store.testMail('mail-1')
    await store.removeMail('mail-1')

    const scope = { params: { scope: 'personal' } }
    expect(api.get).toHaveBeenCalledWith('/integrations/mail', scope)
    expect(api.post).toHaveBeenCalledWith('/integrations/mail/accounts', payload, scope)
    expect(api.put).toHaveBeenCalledWith(
      '/integrations/mail/accounts/mail-1',
      { name: 'Arbeit', imap_host: 'imap.example.com' },
      scope
    )
    expect(api.post).toHaveBeenCalledWith('/integrations/mail/accounts/mail-1/test', null, scope)
    expect(api.delete).toHaveBeenCalledWith('/integrations/mail/accounts/mail-1', scope)
    expect(store.mail.accounts).toHaveLength(1)
  })
})
