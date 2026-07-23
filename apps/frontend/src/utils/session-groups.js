const GROUPS = [
  { key: 'pinned', label: 'Angepinnt' },
  { key: 'today', label: 'Heute' },
  { key: 'recent', label: 'Letzte 7 Tage' },
  { key: 'older', label: 'Älter' }
]

export function groupSessionsByRecency(sessions, now = new Date()) {
  const today = new Date(now)
  today.setHours(0, 0, 0, 0)

  const recentBoundary = new Date(today)
  recentBoundary.setDate(recentBoundary.getDate() - 7)

  const grouped = Object.fromEntries(GROUPS.map(({ key }) => [key, []]))

  for (const session of sessions) {
    if (session.pinned) {
      grouped.pinned.push(session)
      continue
    }

    const timestamp = session.updatedAt || session.createdAt
    const date = timestamp ? new Date(timestamp) : null

    if (date && !Number.isNaN(date.getTime()) && date >= today) grouped.today.push(session)
    else if (date && !Number.isNaN(date.getTime()) && date >= recentBoundary) grouped.recent.push(session)
    else grouped.older.push(session)
  }

  return GROUPS
    .map(({ key, label }) => ({ key, label, sessions: grouped[key] }))
    .filter((group) => group.sessions.length)
}
