const IMAGE_MIME_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
const INTERNAL_ATTACHMENT_URL = new RegExp(
  `^/api/sessions/${UUID}/messages/${UUID}/attachments/${UUID}$`,
  'i'
)

export function safeAttachmentUrl(value) {
  return typeof value === 'string' && INTERNAL_ATTACHMENT_URL.test(value) ? value : null
}

export function imageAttachments(attachments) {
  if (!Array.isArray(attachments)) return []
  return attachments
    .filter((attachment) => attachment?.kind === 'image' && IMAGE_MIME_TYPES.has(attachment.mimeType))
    .map((attachment) => ({ ...attachment, safeUrl: safeAttachmentUrl(attachment.url) }))
}

export function formatAttachmentSize(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return 'Unbekannte Größe'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toLocaleString('de', { maximumFractionDigits: 1 })} KB`
  return `${(bytes / (1024 * 1024)).toLocaleString('de', { maximumFractionDigits: 1 })} MB`
}

export function attachmentMetadata(attachment) {
  const dimensions = Number(attachment?.width) > 0 && Number(attachment?.height) > 0
    ? `${attachment.width} × ${attachment.height} px`
    : null
  return [dimensions, formatAttachmentSize(attachment?.sizeBytes), attachment?.mimeType]
    .filter(Boolean)
    .join(' · ')
}
