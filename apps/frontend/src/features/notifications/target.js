export function notificationTargetRoute(target) {
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
