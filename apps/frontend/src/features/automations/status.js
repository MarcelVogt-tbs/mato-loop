import { formatAbsoluteDateTime } from 'src/utils/date-time'

export function automationRunStatus(run) {
  return ({
    queued: { label: 'Eingereiht', icon: 'schedule', color: 'primary' },
    running: { label: 'Läuft', icon: 'play_circle', color: 'primary' },
    waiting_approval: { label: 'Freigabe erforderlich', icon: 'pending_actions', color: 'warning' },
    waiting_input: { label: 'Eingabe erforderlich', icon: 'help_outline', color: 'warning' },
    completed: { label: 'Abgeschlossen', icon: 'check_circle', color: 'positive' },
    failed: { label: 'Fehlgeschlagen', icon: 'error', color: 'negative' },
    cancelled: { label: 'Abgebrochen', icon: 'cancel', color: 'grey-7' }
  })[run?.status] || { label: 'Unbekannt', icon: 'help_outline', color: 'grey-7' }
}

export function automationLastRunLabel(automation) {
  const run = automation?.lastRun
  return run ? `Letzter Lauf: ${formatAbsoluteDateTime(run.startedAt)} · ${automationRunStatus(run).label}` : 'Noch nicht ausgeführt'
}

export function automationStatus(automation) {
  if (automation?.effectiveState?.activeRunStatus) return automationRunStatus({ status: automation.effectiveState.activeRunStatus })
  if (!automation) return { label: 'Noch nicht gespeichert', icon: 'edit', color: 'grey-6' }
  if (!automation.enabled) return { label: 'Deaktiviert', icon: 'pause_circle_outline', color: 'grey-6' }
  if (automation.triggers?.length) return { label: 'Wartet auf Ereignisse', icon: 'bolt', color: 'primary' }
  if (!(automation.scheduleCount ?? automation.schedules?.length)) return { label: 'Nur manuell', icon: 'touch_app', color: 'grey-6' }
  if (automation.effectiveState?.enabled && automation.effectiveState.nextRunAt) return { label: 'Geplant', icon: 'schedule', color: 'primary' }
  return { label: 'Keine weiteren Termine', icon: 'event_busy', color: 'grey-6' }
}

export function automationTiming(automation) {
  if (!automation) return ''
  if (!(automation.scheduleCount ?? automation.schedules?.length)) return 'Keine automatischen Starts'
  if (!automation.enabled) return 'Automatische Starts sind deaktiviert.'
  if (automation.effectiveState?.enabled && automation.effectiveState.nextRunAt) return `Nächster Start: ${formatAbsoluteDateTime(automation.effectiveState.nextRunAt)}`
  return 'Kein weiterer Termin geplant; manuelle Starts bleiben möglich.'
}
