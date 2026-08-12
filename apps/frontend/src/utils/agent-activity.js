import { toolIdentity } from 'src/utils/tool-display'

const LABELS = {
  submitting: 'Anfrage wird übergeben',
  queued: 'Ausführung wartet auf den Start',
  preparing_context: 'Kontext und Tools werden vorbereitet',
  compacting_context: 'Gesprächskontext wird verdichtet',
  streaming: 'Antwort wird erstellt',
  preparing_tools: 'Tool-Aufrufe werden vorbereitet',
  waiting_approval: 'Wartet auf Freigabe',
  waiting_input: 'Wartet auf deine Antwort',
  finalizing: 'Antwort wird abgeschlossen',
  reconnecting: 'Verbindung wird wiederhergestellt'
}

function toolProgress(activity) {
  const index = activity?.index
  const total = activity?.total
  if (!Number.isInteger(index) || !Number.isInteger(total) || index < 0 || total < 1 || index >= total) {
    return ''
  }
  return ` (${index + 1} von ${total})`
}

export function agentActivityLabel(activity) {
  if (activity?.phase === 'waiting_model') {
    if (
      Number.isInteger(activity.providerAttempt)
      && Number.isInteger(activity.providerAttemptTotal)
      && activity.providerAttempt >= 2
      && activity.providerAttempt <= activity.providerAttemptTotal
    ) {
      return `Provider-Anfrage: Versuch ${activity.providerAttempt} von ${activity.providerAttemptTotal}`
    }
    return activity.modelRequest > 1
      ? 'Modell wertet Tool-Ergebnisse aus'
      : 'Modell analysiert die Anfrage'
  }
  if (activity?.phase === 'processing_tool') {
    return `Tool wird ausgeführt: ${toolIdentity(activity.tool).label}${toolProgress(activity)}`
  }
  return LABELS[activity?.phase] || 'Agent arbeitet'
}

export function agentActivityDisplayKey(activity) {
  if (activity?.phase === 'waiting_model') {
    return `waiting_model:${activity.modelRequest > 1 ? 'tool_results' : 'request'}`
  }
  if (activity?.phase === 'processing_tool') {
    return `processing_tool:${activity.tool || 'unknown'}:${activity.index ?? ''}:${activity.total ?? ''}`
  }
  return activity?.phase || 'idle'
}

export function agentRunPresentation({
  running = false,
  activity = null,
  streamReasoning = null,
  streamContent = '',
  transcript = null
} = {}) {
  if (!running) return { showActivity: false, announcement: '' }

  const toolItems = (transcript?.messages || []).flatMap((message) => (
    message.workHistory?.entries?.filter((entry) => entry.type === 'tool') || message.toolCalls || []
  ))
  const currentWorkHistory = (transcript?.messages || [])
    .some((message) => message.currentTurn && message.workHistory)
  const currentToolItems = toolItems.filter((item) => item.current)
  const approval = toolItems.find((item) => item.approval)?.approval
    || transcript?.unmatchedApprovals?.[0]
  if (approval) {
    return {
      showActivity: false,
      announcement: approval.title
        ? `Freigabe erforderlich: ${approval.title}`
        : 'Freigabe erforderlich'
    }
  }
  const question = toolItems.find((item) => item.question)?.question
    || transcript?.unmatchedQuestions?.[0]
  if (question) {
    return { showActivity: false, announcement: 'Antwort erforderlich' }
  }

  if (toolItems.some((item) => item.active)) {
    return { showActivity: false, announcement: agentActivityLabel(activity) }
  }
  if (currentToolItems.length && ['processing_tool', 'preparing_tools', 'waiting_approval', 'waiting_input'].includes(activity?.phase)) {
    return { showActivity: false, announcement: agentActivityLabel(activity) }
  }
  if (streamContent) {
    return { showActivity: false, announcement: 'Antwort wird erstellt' }
  }
  if (streamReasoning) {
    return { showActivity: false, announcement: 'Überlegung' }
  }
  if (currentWorkHistory) {
    return { showActivity: false, announcement: agentActivityLabel(activity) }
  }
  if (activity?.phase === 'finalizing') {
    return { showActivity: false, announcement: 'Antwort wird abgeschlossen' }
  }
  return { showActivity: true, announcement: agentActivityLabel(activity) }
}

export function formatActivityDuration(startedAt, now = Date.now()) {
  if (!startedAt) return '0 s'
  const started = new Date(startedAt).getTime()
  if (!Number.isFinite(started)) return '0 s'
  const seconds = Math.max(0, Math.floor((now - started) / 1000))
  if (seconds < 60) return `${seconds} s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${seconds % 60} s`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`
  return `${Math.floor(seconds / 86400)} T ${Math.floor((seconds % 86400) / 3600)} h`
}
