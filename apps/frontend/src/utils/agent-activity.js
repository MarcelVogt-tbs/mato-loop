import { toolIdentity } from 'src/utils/tool-display'

const LABELS = {
  submitting: 'LLM verarbeitet die Anfrage',
  queued: 'LLM verarbeitet die Anfrage',
  preparing_context: 'LLM verarbeitet die Anfrage',
  compacting_context: 'Gesprächskontext wird verdichtet',
  streaming: 'Antwort wird erstellt',
  preparing_tools: 'Tool-Aufrufe werden vorbereitet',
  waiting_approval: 'Wartet auf Freigabe',
  finalizing: 'Antwort wird abgeschlossen',
  reconnecting: 'Verbindung wird wiederhergestellt'
}

export function agentActivityLabel(activity) {
  if (activity?.phase === 'waiting_model') {
    return activity.modelRequest > 1
      ? 'Modell verarbeitet die Tool-Ergebnisse'
      : 'LLM verarbeitet die Anfrage'
  }
  if (activity?.phase === 'processing_tool') {
    return `Tool wird ausgeführt: ${toolIdentity(activity.tool).label}`
  }
  return LABELS[activity?.phase] || 'Agent arbeitet'
}

export function formatActivityDuration(startedAt, now = Date.now()) {
  if (!startedAt) return '0 s'
  const started = new Date(startedAt).getTime()
  if (!Number.isFinite(started)) return '0 s'
  const seconds = Math.max(0, Math.floor((now - started) / 1000))
  if (seconds < 60) return `${seconds} s`
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`
}
