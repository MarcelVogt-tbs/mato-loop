import { expect, it } from 'vitest'
import { automationStatus, automationTiming } from '../src/utils/automation-status'

it('distinguishes manual, paused, exhausted and scheduled definitions from running work', () => {
  const automation = { enabled: true, scheduleCount: 0, effectiveState: { enabled: false, activeRunStatus: null, nextRunAt: null } }
  expect(automationStatus(automation).label).toBe('Nur manuell')
  automation.scheduleCount = 1
  expect(automationStatus(automation).label).toBe('Keine weiteren Termine')
  automation.effectiveState = { enabled: true, nextRunAt: '2027-01-01T08:00:00Z' }
  expect(automationStatus(automation).label).toBe('Geplant')
  automation.enabled = false
  automation.effectiveState.activeRunStatus = 'running'
  expect(automationStatus(automation).label).toBe('Läuft')
  expect(automationTiming(automation)).toContain('Zeitpläne pausiert')
  automation.effectiveState.activeRunStatus = 'waiting_approval'
  expect(automationStatus(automation).label).toBe('Freigabe erforderlich')
  automation.effectiveState.activeRunStatus = null
  expect(automationStatus(automation).label).toBe('Zeitpläne pausiert')
  automation.archivedAt = '2026-12-01T08:00:00Z'
  expect(automationStatus(automation).label).toBe('Archiviert')
  expect(automationTiming(automation)).toContain('Archiviert am')
})

it('accepts full definitions after saving without requiring a status round trip', () => {
  expect(automationStatus({ enabled: true, schedules: [] }).label).toBe('Nur manuell')
  expect(automationStatus({ enabled: false, schedules: [{}] }).label).toBe('Zeitpläne pausiert')
})
