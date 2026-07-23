import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('src/boot/axios', () => ({
  api: { get: vi.fn(), post: vi.fn() }
}))

import { useApprovalStore } from '../src/stores/approvals'
import { api } from 'src/boot/axios'

describe('inline approvals', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('removes an approval after a successful decision', async () => {
    api.post.mockResolvedValue({ data: { status: 'approved' } })
    const approvals = useApprovalStore()
    approvals.add({ id: 'approval-1', sessionId: 'session-1' })

    await approvals.decide('approval-1', 'allow_once')

    expect(approvals.pending).toEqual([])
  })

  it('keeps an approval visible when the decision fails', async () => {
    api.post.mockRejectedValue(new Error('request failed'))
    const approvals = useApprovalStore()
    const approval = { id: 'approval-1', sessionId: 'session-1' }
    approvals.add(approval)

    await expect(approvals.decide('approval-1', 'deny')).rejects.toThrow('request failed')

    expect(approvals.pending).toEqual([approval])
  })

  it('reconciles terminal approval errors', async () => {
    api.post.mockRejectedValue({ response: { data: { error: { code: 'approval.runInactive' } } } })
    api.get.mockResolvedValue({ data: [] })
    const approvals = useApprovalStore()
    approvals.add({ id: 'approval-1', sessionId: 'session-1' })

    await expect(approvals.decide('approval-1', 'deny')).rejects.toBeTruthy()

    expect(api.get).toHaveBeenCalledWith('/approvals/pending')
    expect(approvals.pending).toEqual([])
  })
})
