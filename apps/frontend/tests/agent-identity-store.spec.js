import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), patch: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useAgentIdentityStore } from '../src/stores/agent-identity'

describe('agent identity store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('uses MatoLoop as a synchronous fallback and loads the configured identity', async () => {
    api.get.mockResolvedValue({
      data: { displayName: 'Ada', instructions: 'Antworte direkt.' }
    })
    const identity = useAgentIdentityStore()

    expect(identity.displayName).toBe('MatoLoop')
    await identity.fetchIdentity()

    expect(api.get).toHaveBeenCalledWith('/agent-identity', undefined)
    expect(identity.displayName).toBe('Ada')
    expect(identity.instructions).toBe('Antworte direkt.')
    expect(identity.loaded).toBe(true)
  })

  it('persists both editable fields and adopts the canonical response', async () => {
    api.patch.mockResolvedValue({
      data: { displayName: 'Ada', instructions: 'Sei freundlich.' }
    })
    const identity = useAgentIdentityStore()

    await identity.saveIdentity({
      displayName: ' Ada ',
      instructions: ' Sei freundlich. '
    })

    expect(api.patch).toHaveBeenCalledWith('/agent-identity', {
      display_name: ' Ada ',
      instructions: ' Sei freundlich. '
    })
    expect(identity.displayName).toBe('Ada')
    expect(identity.instructions).toBe('Sei freundlich.')
  })

  it('can apply an identity update received through SSE', () => {
    const identity = useAgentIdentityStore()

    identity.assignIdentity({ displayName: 'Kira', instructions: 'Bleib neugierig.' })

    expect(identity.displayName).toBe('Kira')
    expect(identity.instructions).toBe('Bleib neugierig.')
  })

  it('does not let an older fetch overwrite an SSE update', async () => {
    let resolveFetch
    api.get.mockImplementation(() => new Promise((resolve) => { resolveFetch = resolve }))
    const identity = useAgentIdentityStore()

    const loading = identity.fetchIdentity()
    identity.assignIdentity({ displayName: 'Neu', instructions: 'Aktuell' })
    resolveFetch({ data: { displayName: 'Alt', instructions: 'Veraltet' } })
    await loading

    expect(identity.displayName).toBe('Neu')
    expect(identity.instructions).toBe('Aktuell')
  })
})
