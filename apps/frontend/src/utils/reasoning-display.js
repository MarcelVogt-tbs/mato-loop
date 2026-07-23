export function reasoningTitle(reasoning, running = false, activityLabel = '') {
  if (running && activityLabel) return activityLabel
  return reasoning?.kind === 'summary'
    ? 'Begründungszusammenfassung anzeigen'
    : 'Denkprozess anzeigen'
}

export function formatReasoningDuration(milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null
  if (milliseconds < 1000) return `${milliseconds} ms`
  return `${(milliseconds / 1000).toFixed(1).replace('.0', '')} s`
}
