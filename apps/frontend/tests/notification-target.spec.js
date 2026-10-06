import { describe, expect, it } from 'vitest'
import { notificationTargetRoute } from '../src/utils/notification-target'

describe('notification target navigation', () => {
  it('keeps session navigation unchanged', () => {
    expect(notificationTargetRoute({ type: 'session', sessionId: 'session-1' })).toEqual({
      name: 'chat', query: { session: 'session-1' }
    })
  })

  it('opens the exact automation run', () => {
    expect(notificationTargetRoute({
      type: 'automationRun', automationId: 'automation-1', runId: 'run-1'
    })).toEqual({
      name: 'settings-automation-detail',
      params: { automationId: 'automation-1' },
      query: { run: 'run-1' }
    })
  })

  it('opens the exact Dreaming proposal', () => {
    expect(notificationTargetRoute({ type: 'memoryApproval', approvalId: 'approval-1' })).toEqual({
      name: 'settings-memory', query: { tab: 'dreaming', approval: 'approval-1' }
    })
  })

  it('ignores incomplete and unknown targets', () => {
    expect(notificationTargetRoute({ type: 'automationRun', automationId: 'automation-1' })).toBeNull()
    expect(notificationTargetRoute({ type: 'unknown' })).toBeNull()
  })
})
