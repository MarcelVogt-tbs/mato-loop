const GROUPS = [
  { key: 'pinned', label: 'Angepinnt' },
  { key: 'today', label: 'Heute' },
  { key: 'yesterday', label: 'Gestern' },
  { key: 'recent', label: 'Letzte 7 Tage' },
  { key: 'older', label: 'Älter' }
]

export function groupSessionsByRecency(sessions, now = new Date()) {
  const today = new Date(now)
  today.setHours(0, 0, 0, 0)

  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)

  const recentBoundary = new Date(today)
  recentBoundary.setDate(recentBoundary.getDate() - 7)

  const grouped = Object.fromEntries(GROUPS.map(({ key }) => [key, []]))

  for (const session of sessions) {
    if (session.pinned) {
      grouped.pinned.push(session)
      continue
    }

    const timestamp = session.lastMessageAt || session.createdAt
    const date = timestamp ? new Date(timestamp) : null

    if (date && !Number.isNaN(date.getTime()) && date >= today) grouped.today.push(session)
    else if (date && !Number.isNaN(date.getTime()) && date >= yesterday) grouped.yesterday.push(session)
    else if (date && !Number.isNaN(date.getTime()) && date >= recentBoundary) grouped.recent.push(session)
    else grouped.older.push(session)
  }

  return GROUPS
    .map(({ key, label }) => ({ key, label, sessions: grouped[key] }))
    .filter((group) => group.sessions.length)
}

export function sessionSidebarStatus(session, approvalCount = 0, questionCount = 0) {
  if (approvalCount > 0) {
    return {
      kind: 'approval',
      count: approvalCount,
      label: approvalCount === 1
        ? 'Freigabe erforderlich'
        : `${approvalCount} Freigaben erforderlich`
    }
  }
  if (questionCount > 0) {
    return {
      kind: 'approval',
      count: questionCount,
      label: questionCount === 1 ? 'Antwort erforderlich' : `${questionCount} Antworten erforderlich`
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
