export function reasoningTitle(reasoning, running = false, activityLabel = '') {
  if (running && activityLabel) return activityLabel
  return reasoning?.kind === 'summary'
    ? 'Zusammenfassung'
    : 'Überlegung'
}

export function formatReasoningDuration(milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null
  if (milliseconds < 1000) return `${milliseconds} ms`
  return `${(milliseconds / 1000).toFixed(1).replace('.0', '')} s`
}

function strongSegments(text) {
  const segments = []
  const pattern = /\*\*([^*\n]+)\*\*/g
  let cursor = 0
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) segments.push({ text: text.slice(cursor, match.index), strong: false })
    segments.push({ text: match[1], strong: true })
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), strong: false })
  return segments.length ? segments : [{ text, strong: false }]
}

export function reasoningBlocks(reasoning) {
  const text = String(reasoning?.text || '')
  if (!text) return []
  if (reasoning?.kind !== 'summary') return [[{ text, strong: false }]]
  return text
    .replace(/\*{4}/g, '**\n\n**')
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map(strongSegments)
}

export function reasoningStatusLabel(reasoning, maxLength = 160) {
  if (reasoning?.kind !== 'summary') return null
  const blocks = reasoningBlocks(reasoning)
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const block = blocks[index]
    const firstText = block.find((segment) => segment.text.trim())
    const source = firstText?.strong
      ? block.filter((segment) => segment.strong).map((segment) => segment.text).join(' ')
      : block.map((segment) => segment.text).join('')
    const text = source
      .replace(/^\*{1,2}|\*{1,2}$/g, '')
      .replace(/\s+/g, ' ')
      .trim()
    if (text) return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
  }
  return null
}

export function reasoningHistoryPresentation(reasoning, live = false) {
  return {
    title: reasoning?.status === 'interrupted'
      ? 'Überlegung unterbrochen'
      : live ? 'Modell überlegt' : 'Überlegt',
    summary: reasoningStatusLabel(reasoning),
    duration: formatReasoningDuration(reasoning?.observedDurationMs)
  }
}
