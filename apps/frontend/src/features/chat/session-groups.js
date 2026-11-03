export function groupSessionsByRecency(sessions) {
  return [
    { key: 'pinned', label: 'Angepinnt', sessions: sessions.filter((session) => session.pinned) },
    { key: 'recent', label: 'Zuletzt', sessions: sessions.filter((session) => !session.pinned) }
  ].filter((group) => group.sessions.length)
}

export function sessionSidebarStatus(session, approvalCount = 0, questionCount = 0) {
  const count = approvalCount + questionCount
  if (count > 0) {
    return {
      kind: 'approval',
      count,
      label: count === 1 ? '1 offene Anfrage' : `${count} offene Anfragen`
    }
  }
  if (session?.activeRunStatus === 'queued' || session?.activeRunStatus === 'running') {
    return { kind: 'active', count: 0, label: 'Agent arbeitet' }
  }
  if (session?.activeRunStatus === 'waiting_approval') {
    return { kind: 'approval', count: 0, label: 'Freigabe erforderlich' }
  }
  if (session?.activeRunStatus === 'waiting_input') {
    return { kind: 'approval', count: 0, label: 'Antwort erforderlich' }
  }
  return null
}
