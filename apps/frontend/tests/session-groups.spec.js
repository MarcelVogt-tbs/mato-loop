import { describe, expect, it } from 'vitest'

import { groupSessionsByRecency, sessionSidebarStatus } from '../src/utils/session-groups'

describe('session grouping', () => {
  it('keeps pinned sessions first and preserves the server order within each group', () => {
    const sessions = [
      { id: 'recent', lastMessageAt: '2026-07-20T08:00:00' },
      { id: 'pinned', lastMessageAt: '2026-06-01T08:00:00', pinned: true },
      { id: 'older', lastMessageAt: '2026-07-12T23:59:59' },
      { id: 'unknown' }
    ]

    const groups = groupSessionsByRecency(sessions)

    expect(groups.map((group) => [group.label, group.sessions.map((session) => session.id)])).toEqual([
      ['Angepinnt', ['pinned']],
      ['Zuletzt', ['recent', 'older', 'unknown']]
    ])
  })

  it('omits empty groups', () => {
    expect(groupSessionsByRecency([])).toEqual([])
  })
})

describe('session sidebar status', () => {
  it('shares one slot and prioritizes approvals over active model work', () => {
    expect(sessionSidebarStatus({ activeRunStatus: 'running' }, 2)).toEqual({
      kind: 'approval',
      count: 2,
      label: '2 Freigaben erforderlich'
    })
    expect(sessionSidebarStatus({ activeRunStatus: 'running' })).toEqual({
      kind: 'active',
      count: 0,
      label: 'Agent arbeitet'
    })
    expect(sessionSidebarStatus({ activeRunStatus: 'waiting_approval' })).toEqual({
      kind: 'approval',
      count: 0,
      label: 'Freigabe erforderlich'
    })
    expect(sessionSidebarStatus({ activeRunStatus: null })).toBeNull()
  })
})
