import { describe, expect, it } from 'vitest'

import { groupSessionsByRecency, sessionSidebarStatus } from '../src/utils/session-groups'

describe('session grouping', () => {
  it('groups sessions by their local calendar date', () => {
    const sessions = [
      { id: 'today', lastMessageAt: '2026-07-20T08:00:00' },
      { id: 'yesterday', lastMessageAt: '2026-07-19T18:00:00' },
      { id: 'recent', lastMessageAt: '2026-07-14T18:00:00' },
      { id: 'recent-boundary', lastMessageAt: '2026-07-13T00:00:00' },
      { id: 'older', lastMessageAt: '2026-07-12T23:59:59' }
    ]

    const groups = groupSessionsByRecency(sessions, new Date('2026-07-20T12:00:00'))

    expect(groups.map((group) => group.label)).toEqual(['Heute', 'Gestern', 'Letzte 7 Tage', 'Älter'])
    expect(groups.map((group) => group.sessions.map((session) => session.id))).toEqual([
      ['today'],
      ['yesterday'],
      ['recent', 'recent-boundary'],
      ['older']
    ])
  })

  it('uses createdAt as fallback and omits empty groups', () => {
    const groups = groupSessionsByRecency([
      { id: 'created', createdAt: '2026-07-20T06:00:00' },
      { id: 'unknown' }
    ], new Date('2026-07-20T12:00:00'))

    expect(groups).toEqual([
      {
        key: 'today',
        label: 'Heute',
        sessions: [{ id: 'created', createdAt: '2026-07-20T06:00:00' }]
      },
      {
        key: 'older',
        label: 'Älter',
        sessions: [{ id: 'unknown' }]
      }
    ])
  })

  it('keeps pinned sessions in their own first group', () => {
    const groups = groupSessionsByRecency([
      { id: 'today', lastMessageAt: '2026-07-20T08:00:00', pinned: false },
      { id: 'pinned', lastMessageAt: '2026-06-01T08:00:00', pinned: true }
    ], new Date('2026-07-20T12:00:00'))

    expect(groups.map((group) => group.label)).toEqual(['Angepinnt', 'Heute'])
    expect(groups[0].sessions.map((session) => session.id)).toEqual(['pinned'])
  })

  it('ignores metadata updates when grouping sessions', () => {
    const groups = groupSessionsByRecency([{
      id: 'older-message',
      lastMessageAt: '2026-07-01T08:00:00',
      updatedAt: '2026-07-20T08:00:00'
    }], new Date('2026-07-20T12:00:00'))

    expect(groups[0].label).toBe('Älter')
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
