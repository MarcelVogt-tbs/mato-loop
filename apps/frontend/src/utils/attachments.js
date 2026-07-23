const ATTACHMENT_KINDS = new Set(['image', 'audio', 'text', 'document', 'file'])
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
const INTERNAL_ATTACHMENT_URL = new RegExp(
  `^/api/sessions/${UUID}/messages/${UUID}/attachments/${UUID}$`,
  'i'
)

export function safeAttachmentUrl(value) {
  return typeof value === 'string' && INTERNAL_ATTACHMENT_URL.test(value) ? value : null
}

export function imageAttachments(attachments) {
  return supportedAttachments(attachments).filter((attachment) => attachment.kind === 'image')
}

export function audioAttachments(attachments) {
  return supportedAttachments(attachments).filter((attachment) => attachment.kind === 'audio')
}

export function supportedAttachments(attachments) {
  if (!Array.isArray(attachments)) return []
  return attachments.flatMap((attachment) => {
    if (!attachment || typeof attachment !== 'object') return []
    if (!ATTACHMENT_KINDS.has(attachment.kind)) return []
    const mimeType = typeof attachment.mimeType === 'string'
      ? attachment.mimeType.split(';', 1)[0].trim().toLowerCase()
      : ''
    return [{
      ...attachment,
      mimeType,
      filename: typeof attachment.filename === 'string' ? attachment.filename : null,
      metadata: attachment.metadata && typeof attachment.metadata === 'object' && !Array.isArray(attachment.metadata)
        ? attachment.metadata
        : {},
      safeUrl: safeAttachmentUrl(attachment.url)
    }]
  })
}

export function audioMimeTypeSupported(value) {
  const mimeType = typeof value === 'string'
    ? value.split(';', 1)[0].trim().toLowerCase()
    : ''
  if (!mimeType.startsWith('audio/')) return false
  const audio = globalThis.document?.createElement?.('audio')
  return typeof audio?.canPlayType === 'function' && audio.canPlayType(mimeType) !== ''
}

export function attachmentIcon(attachment) {
  if (attachment?.kind === 'image') return 'image'
  if (attachment?.kind === 'audio') return 'audio_file'
  const mimeType = String(attachment?.mimeType || '')
  if (mimeType === 'application/pdf') return 'picture_as_pdf'
  if (attachment?.kind === 'text' || mimeType.startsWith('text/')) return 'description'
  if (mimeType.includes('spreadsheet') || mimeType.includes('excel')) return 'table_view'
  if (mimeType.includes('presentation') || mimeType.includes('powerpoint')) return 'slideshow'
  if (mimeType.includes('zip') || mimeType.includes('compressed')) return 'folder_zip'
  return attachment?.kind === 'document' ? 'article' : 'draft'
}

export function formatAttachmentSize(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return 'Unbekannte Größe'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toLocaleString('de', { maximumFractionDigits: 1 })} KB`
  return `${(bytes / (1024 * 1024)).toLocaleString('de', { maximumFractionDigits: 1 })} MB`
}

export function formatAttachmentDuration(value) {
  const milliseconds = Number(value)
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null
  const totalSeconds = Math.round(milliseconds / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

export function attachmentMetadata(attachment) {
  const dimensions = Number(attachment?.width) > 0 && Number(attachment?.height) > 0
    ? `${attachment.width} × ${attachment.height} px`
    : null
  const duration = attachment?.kind === 'audio'
    ? formatAttachmentDuration(attachment.durationMs ?? attachment.metadata?.durationMs)
    : null
  return [dimensions, duration, formatAttachmentSize(attachment?.sizeBytes), attachment?.mimeType]
    .filter(Boolean)
    .join(' · ')
}
