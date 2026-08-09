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
    expect(api.post).toHaveBeenCalledWith('/approvals/approval-1/decisions', {
      decision: 'allow_once'
    })
  })

  it('sends the expected draft revision only for allow decisions', async () => {
    api.post.mockResolvedValue({ data: { status: 'approved' } })
    const approvals = useApprovalStore()
    approvals.add({ id: 'allow', sessionId: 'session-1' })
    approvals.add({ id: 'deny', sessionId: 'session-1' })

    await approvals.decide('allow', 'allow_once', 'revision-2')
    await approvals.decide('deny', 'deny', 'revision-2')

    expect(api.post).toHaveBeenNthCalledWith(1, '/approvals/allow/decisions', {
      decision: 'allow_once', expected_revision: 'revision-2'
    })
    expect(api.post).toHaveBeenNthCalledWith(2, '/approvals/deny/decisions', {
      decision: 'deny'
    })
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

  it('forwards request options when loading session permissions', async () => {
    api.get.mockResolvedValue({ data: [{ id: 'grant-1' }] })
    const approvals = useApprovalStore()

    const result = await approvals.permissions('session-1', { suppressErrorToast: true })

    expect(result).toEqual([{ id: 'grant-1' }])
    expect(api.get).toHaveBeenCalledWith(
      '/approvals/sessions/session-1/permissions',
      { suppressErrorToast: true }
    )
  })
})
