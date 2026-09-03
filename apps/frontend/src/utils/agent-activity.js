import { reasoningStatusLabel } from 'src/utils/reasoning-display'
import { toolActivityLabel, toolSummary } from 'src/utils/tool-display'

const LABELS = {
  submitting: 'Verarbeite Anfrage',
  queued: 'Verarbeite Anfrage',
  preparing_context: 'Verarbeite Anfrage',
  compacting_context: 'Kontext verdichten',
  waiting_approval: 'Freigabe erforderlich',
  waiting_input: 'Antwort erforderlich',
  finalizing: 'Abschließen',
  reconnecting: 'Neu verbinden'
}

const WARNING_PRESENTATIONS = {
  'privacy.placeholderRestorationIncomplete': {
    title: 'Geschützter Wert nicht verfügbar',
    summary: 'Ein Datenschutz-Platzhalter konnte nicht wiederhergestellt werden.'
  }
}

export function runWarningEntries(warnings = [], keyPrefix = 'warning', live = false) {
  return warnings.flatMap((warning, index) => {
    const code = typeof warning === 'string' ? warning : warning?.code
    const presentation = WARNING_PRESENTATIONS[code]
    if (!presentation) return []
    return [{
      key: `${keyPrefix}-${warning?.modelRequest ?? index}-${code}`,
      type: 'warning',
      warning: true,
      live,
      presentation
    }]
  })
}

export function agentActivityLabel(activity) {
  const suppliedLabel = typeof activity?.activityLabel === 'string' ? activity.activityLabel.trim() : ''
  if (suppliedLabel) return activity.activityLabel
  if (['preparing_context', 'waiting_model'].includes(activity?.phase)) {
    return activity.modelRequest > 1 ? 'Verarbeite Tool-Ergebnisse' : 'Verarbeite Anfrage'
  }
  if (activity?.phase === 'processing_tool') {
    return toolActivityLabel(activity.tool)
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
  stopping = false,
  activity = null,
  streamReasoning = null,
  streamContent = '',
  transcript = null
} = {}) {
  if (!running) return { showActivity: false, label: '', summary: '', announcement: '' }

  const toolItems = (transcript?.messages || []).flatMap((message) => (
    message.workHistory?.entries?.filter((entry) => entry.type === 'tool') || message.toolCalls || []
  ))
  const currentToolItems = toolItems.filter((item) => item.current)
  const activeTool = toolItems.find((item) => item.active)
    || currentToolItems.find((item) => item.tool?.id === activity?.toolCallId)
  const approval = toolItems.find((item) => item.approval)?.approval
    || transcript?.unmatchedApprovals?.[0]
  const question = toolItems.find((item) => item.question)?.question
    || transcript?.unmatchedQuestions?.[0]
  if (
    !stopping
    && !approval
    && !question
    && (streamContent || ['preparing_tools', 'finalizing'].includes(activity?.phase))
  ) {
    return { showActivity: false, label: '', summary: '', announcement: 'Antwort' }
  }
  const suppliedReasoningLabel = typeof activity?.reasoningLabel === 'string'
    ? activity.reasoningLabel.trim()
    : ''
  const reasoningLabel = activity?.phase === 'waiting_model' && !streamContent
    && (streamReasoning || suppliedReasoningLabel)
    ? suppliedReasoningLabel || reasoningStatusLabel(streamReasoning) || 'Nachdenken'
    : ''
  const label = stopping
    ? 'Anfrage abbrechen'
    : approval
      ? 'Freigabe erforderlich'
      : question ? 'Antwort erforderlich' : reasoningLabel || agentActivityLabel(activity)
  const summary = activity?.phase === 'compacting_context'
    ? ''
    : approval?.title
      || question?.questions?.[0]?.question
      || (activeTool ? toolSummary(activeTool.tool) : '')
      || ''
  let announcement = label
  if (approval) {
    announcement = approval.title
      ? `Freigabe erforderlich: ${approval.title}`
      : 'Freigabe erforderlich'
  } else if (question) {
    announcement = 'Antwort erforderlich'
  } else if (streamReasoning || suppliedReasoningLabel) {
    announcement = 'Nachdenken'
  }
  return { showActivity: true, label, summary, announcement }
}

export function activityDurationMs(history, running, now = Date.now()) {
  if (!Number.isInteger(history?.activeDurationMs) || history.activeDurationMs < 0) return null
  const activeDurationUpdatedAt = Date.parse(history.activeDurationUpdatedAt || '')
  return Math.max(0, history.activeDurationMs + (
    running && Number.isFinite(activeDurationUpdatedAt) ? now - activeDurationUpdatedAt : 0
  ))
}

export function formatActivityDuration(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  if (seconds < 60) return `${seconds} s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${seconds % 60} s`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`
  return `${Math.floor(seconds / 86400)} T ${Math.floor((seconds % 86400) / 3600)} h`
}
