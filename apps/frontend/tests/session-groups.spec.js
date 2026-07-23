import { describe, expect, it } from 'vitest'

import { groupSessionsByRecency } from '../src/utils/session-groups'

describe('session grouping', () => {
  it('groups sessions by their local calendar date', () => {
    const sessions = [
      { id: 'today', updatedAt: '2026-07-20T08:00:00' },
      { id: 'recent', updatedAt: '2026-07-14T18:00:00' },
      { id: 'older', updatedAt: '2026-07-12T23:59:59' }
    ]

    const groups = groupSessionsByRecency(sessions, new Date('2026-07-20T12:00:00'))

    expect(groups.map((group) => group.label)).toEqual(['Heute', 'Letzte 7 Tage', 'Älter'])
    expect(groups.map((group) => group.sessions.map((session) => session.id))).toEqual([
      ['today'],
      ['recent'],
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
      { id: 'today', updatedAt: '2026-07-20T08:00:00', pinned: false },
      { id: 'pinned', updatedAt: '2026-06-01T08:00:00', pinned: true }
    ], new Date('2026-07-20T12:00:00'))

    expect(groups.map((group) => group.label)).toEqual(['Angepinnt', 'Heute'])
    expect(groups[0].sessions.map((session) => session.id)).toEqual(['pinned'])
  })
})
