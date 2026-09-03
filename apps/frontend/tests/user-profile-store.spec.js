import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), patch: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import {
  profileFactDraft,
  profileFactPayload,
  useUserProfileStore
} from '../src/stores/user-profile'

const profileResponse = {
  fullName: { both: null, privateOnly: 'Ada Lovelace', publicOnly: null },
  preferredName: { both: 'Ada', privateOnly: null, publicOnly: 'A.' },
  formOfAddress: { both: 'du', privateOnly: null, publicOnly: null },
  pronouns: { both: null, privateOnly: 'sie/ihr', publicOnly: null },
  primaryLanguage: { both: 'Deutsch', privateOnly: null, publicOnly: null },
  primaryResidence: { both: null, privateOnly: 'Berlin', publicOnly: null }
}

describe('user profile store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('maps audience values to the two-field editor without losing effective values', () => {
    expect(profileFactDraft({ both: 'Ada', privateOnly: null, publicOnly: 'A.' })).toEqual({
      privateValue: 'Ada',
      publicValue: 'A.',
      shared: false
    })
    expect(profileFactDraft({ both: null, privateOnly: null, publicOnly: null }).shared).toBe(false)
    expect(profileFactPayload({ privateValue: ' Ada ', publicValue: 'Ignored', shared: true })).toEqual({
      both: 'Ada',
      privateOnly: null,
      publicOnly: null
    })
    expect(profileFactPayload({ privateValue: 'Ada', publicValue: 'A.', shared: false })).toEqual({
      both: null,
      privateOnly: 'Ada',
      publicOnly: 'A.'
    })
  })

  it('loads the canonical audience-specific profile', async () => {
    api.get.mockResolvedValue({ data: profileResponse })
    const profile = useUserProfileStore()

    await profile.fetchProfile()

    expect(api.get).toHaveBeenCalledWith('/user-profile', undefined)
    expect(profile.preferredName).toEqual(profileResponse.preferredName)
    expect(profile.fullName.privateOnly).toBe('Ada Lovelace')
    expect(profile.primaryResidence.privateOnly).toBe('Berlin')
    expect(profile.loaded).toBe(true)
  })

  it('defaults fields missing from older profile responses', async () => {
    const olderResponse = { ...profileResponse }
    delete olderResponse.fullName
    api.get.mockResolvedValue({ data: olderResponse })
    const profile = useUserProfileStore()

    await profile.fetchProfile()

    expect(profile.fullName).toEqual({ both: null, privateOnly: null, publicOnly: null })
  })

  it('patches only supplied changes', async () => {
    api.get.mockResolvedValue({ data: profileResponse })
    api.patch.mockResolvedValue({
      data: {
        ...profileResponse,
        preferredName: { ...profileResponse.preferredName, publicOnly: null }
      }
    })
    const profile = useUserProfileStore()
    await profile.fetchProfile()

    await profile.saveProfile({ preferredName: { publicOnly: null } })

    expect(api.patch).toHaveBeenCalledWith('/user-profile', {
      preferredName: { publicOnly: null }
    })
    expect(profile.preferredName.publicOnly).toBeNull()
  })

  it('keeps the canonical state when an update fails', async () => {
    api.get.mockResolvedValue({ data: profileResponse })
    api.patch.mockRejectedValue({ response: { status: 409 } })
    const profile = useUserProfileStore()
    await profile.fetchProfile()

    await expect(profile.saveProfile({ preferredName: { both: 'Grace' } })).rejects.toEqual({
      response: { status: 409 }
    })

    expect(profile.preferredName.both).toBe('Ada')
    expect(profile.saving).toBe(false)
  })
})
