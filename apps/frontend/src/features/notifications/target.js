export function notificationTargetRoute(target) {
  if (target?.type === 'memoryApproval' && target.approvalId) {
    if (target.sessionId) return { name: 'chat', query: { session: target.sessionId } }
    // Older persisted notices need their owner-scoped approval resolved first.
    return { name: 'settings-memory', query: { tab: 'dreaming', approval: target.approvalId } }
  }
  if (target?.type === 'session' && target.sessionId) {
    return { name: 'chat', query: { session: target.sessionId } }
  }
  if (target?.type === 'automationRun' && target.automationId && target.runId) {
    return {
      name: 'settings-automation-detail',
      params: { automationId: target.automationId },
      query: { run: target.runId }
    }
  }
  return null
}
