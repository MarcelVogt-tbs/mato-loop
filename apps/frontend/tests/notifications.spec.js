import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  dismiss: vi.fn(),
  create: vi.fn()
}))

vi.mock('quasar', () => ({
  Notify: { create: mocks.create }
}))

import { notifyBackendReachable, notifyBackendUnavailable } from '../src/utils/notifications'

describe('backend availability notification', () => {
  afterEach(() => {
    notifyBackendReachable()
    vi.clearAllMocks()
  })

  it('keeps one notification visible until the backend is reachable', () => {
    mocks.create.mockReturnValue(mocks.dismiss)

    notifyBackendUnavailable()
    notifyBackendUnavailable()

    expect(mocks.create).toHaveBeenCalledOnce()
    expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ timeout: 0, group: false }))
    expect(mocks.dismiss).not.toHaveBeenCalled()

    notifyBackendReachable()

    expect(mocks.dismiss).toHaveBeenCalledOnce()
  })
})
