import { formatAbsoluteDateTime } from './date-time'

export function automationStatus(automation) {
  const active = {
    queued: { label: 'Eingereiht', icon: 'schedule', color: 'primary' },
    running: { label: 'Läuft', icon: 'play_circle', color: 'primary' },
    waiting_approval: { label: 'Freigabe erforderlich', icon: 'pending_actions', color: 'warning' },
    waiting_input: { label: 'Eingabe erforderlich', icon: 'help_outline', color: 'warning' }
  }[automation?.effectiveState?.activeRunStatus]
  if (active) return active
  if (!automation) return { label: 'Noch nicht gespeichert', icon: 'edit', color: 'grey-6' }
  if (automation.archivedAt) return { label: 'Archiviert', icon: 'archive', color: 'grey-6' }
  if (!(automation.scheduleCount ?? automation.schedules?.length)) return { label: 'Nur manuell', icon: 'touch_app', color: 'grey-6' }
  if (!automation.enabled) return { label: 'Zeitpläne pausiert', icon: 'pause_circle_outline', color: 'grey-6' }
  if (automation.effectiveState?.enabled && automation.effectiveState.nextRunAt) return { label: 'Geplant', icon: 'schedule', color: 'primary' }
  return { label: 'Keine weiteren Termine', icon: 'event_busy', color: 'grey-6' }
}

export function automationTiming(automation) {
  if (!automation) return ''
  if (automation.archivedAt) return `Archiviert am ${formatAbsoluteDateTime(automation.archivedAt)}`
  if (!(automation.scheduleCount ?? automation.schedules?.length)) return 'Keine automatischen Starts'
  if (!automation.enabled) return 'Zeitpläne pausiert; manuelle Starts bleiben möglich.'
  if (automation.effectiveState?.enabled && automation.effectiveState.nextRunAt) return `Nächster Start: ${formatAbsoluteDateTime(automation.effectiveState.nextRunAt)}`
  return 'Kein weiterer Termin geplant; manuelle Starts bleiben möglich.'
}
