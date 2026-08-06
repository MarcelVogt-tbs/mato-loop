const ATTACHMENT_KINDS = new Set(['image', 'audio', 'video', 'text', 'document', 'file'])
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
const INTERNAL_ATTACHMENT_URL = new RegExp(
  `^/api/sessions/${UUID}/messages/${UUID}/attachments/${UUID}$`,
  'i'
)
const INTERNAL_FILE_DOWNLOAD_PATH = '/api/workspace/files/download'

function validRelativePath(value, allowRoot = false) {
  if (value === '.') return allowRoot
  return typeof value === 'string'
    && value.length > 0
    && !value.includes('\\')
    && ![...value].some((character) => {
      const code = character.charCodeAt(0)
      return code < 32 || (code >= 127 && code <= 159)
    })
    && value.split('/').every((part) => part && part !== '.' && part !== '..')
}

export function safeAttachmentUrl(value) {
  return typeof value === 'string' && INTERNAL_ATTACHMENT_URL.test(value) ? value : null
}

export function safeFileDownloadUrl(value) {
  if (typeof value !== 'string' || !value.startsWith(`${INTERNAL_FILE_DOWNLOAD_PATH}?`)) return null
  let parsed
  try {
    parsed = new URL(value, 'https://mato-loop.invalid')
  } catch {
    return null
  }
  const keys = [...parsed.searchParams.keys()]
  const allowedKeys = new Set(['path', 'scope', 'session_id', 'root_identity'])
  if (
    parsed.origin !== 'https://mato-loop.invalid'
    || parsed.pathname !== INTERNAL_FILE_DOWNLOAD_PATH
    || parsed.hash
    || keys.length !== allowedKeys.size
    || new Set(keys).size !== keys.length
    || keys.some((key) => !allowedKeys.has(key))
  ) return null
  const path = parsed.searchParams.get('path')
  const scope = parsed.searchParams.get('scope')
  const sessionId = parsed.searchParams.get('session_id')
  const rootIdentity = parsed.searchParams.get('root_identity')
  if (!validRelativePath(path) || !new RegExp(`^${UUID}$`, 'i').test(sessionId || '')) return null
  if (scope === 'session') return rootIdentity === sessionId ? value : null
  return scope === 'filesystem' && validRelativePath(rootIdentity, true) ? value : null
}

export function toolFileAttachment(tool, result) {
  if (tool?.name !== 'provide_file' || typeof result?.content !== 'string') return null
  let value
  try {
    value = JSON.parse(result.content)
  } catch {
    return null
  }
  const safeUrl = safeFileDownloadUrl(value?.downloadUrl)
  const size = Number(value?.sizeBytes)
  const path = typeof value?.path === 'string' ? value.path : ''
  const filename = typeof value?.filename === 'string' ? value.filename : ''
  const expectedScope = value?.root === 'session' ? 'session' : value?.root === 'workspace' ? 'filesystem' : null
  if (
    !safeUrl
    || !expectedScope
    || !Number.isSafeInteger(size)
    || size < 0
    || filename !== path.split('/').at(-1)
    || filename.length > 255
  ) return null
  const params = new URL(safeUrl, 'https://mato-loop.invalid').searchParams
  if (params.get('scope') !== expectedScope || params.get('path') !== path) return null
  return {
    id: `provided-${result.id || tool.id || path}`,
    kind: 'file',
    mimeType: 'application/octet-stream',
    sizeBytes: size,
    filename,
    url: safeUrl
  }
}

export function toolFileActions(result) {
  if (!Array.isArray(result?.fileActions) || result.fileActions.length > 1) return []
  return result.fileActions.flatMap((action) => {
    if (!action || typeof action !== 'object') return []
    const safeUrl = safeFileDownloadUrl(action.downloadUrl)
    const size = Number(action.sizeBytes)
    const path = typeof action.path === 'string' ? action.path : ''
    const filename = typeof action.filename === 'string' ? action.filename : ''
    const rootIdentity = typeof action.rootIdentity === 'string' ? action.rootIdentity : ''
    const scope = action.root === 'session' ? 'session' : action.root === 'workspace' ? 'filesystem' : null
    if (
      !safeUrl
      || !scope
      || !['write', 'copy', 'move'].includes(action.operation)
      || !Number.isSafeInteger(size)
      || size < 0
      || filename !== path.split('/').at(-1)
      || filename.length > 255
    ) return []
    const params = new URL(safeUrl, 'https://mato-loop.invalid').searchParams
    const sessionId = params.get('session_id')
    if (
      params.get('scope') !== scope
      || params.get('path') !== path
      || params.get('root_identity') !== rootIdentity
      || !sessionId
    ) return []
    return [{
      operation: action.operation,
      root: action.root,
      rootIdentity,
      path,
      filename,
      sizeBytes: size,
      sessionId,
      downloadUrl: safeUrl
    }]
  })
}

export function imageAttachments(attachments) {
  return supportedAttachments(attachments).filter((attachment) => attachment.kind === 'image')
}

export function audioAttachments(attachments) {
  return supportedAttachments(attachments).filter((attachment) => attachment.kind === 'audio')
}

export function videoAttachments(attachments) {
  return supportedAttachments(attachments).filter((attachment) => attachment.kind === 'video')
}

export function supportedAttachments(attachments) {
  if (!Array.isArray(attachments)) return []
  return attachments.flatMap((attachment) => {
    if (!attachment || typeof attachment !== 'object') return []
    if (!ATTACHMENT_KINDS.has(attachment.kind)) return []
    const mimeType = typeof attachment.mimeType === 'string'
      ? attachment.mimeType.split(';', 1)[0].trim().toLowerCase()
      : ''
    const safeUrl = safeAttachmentUrl(attachment.url)
      || (attachment.kind === 'file' ? safeFileDownloadUrl(attachment.url) : null)
    return [{
      ...attachment,
      mimeType,
      filename: typeof attachment.filename === 'string' ? attachment.filename : null,
      metadata: attachment.metadata && typeof attachment.metadata === 'object' && !Array.isArray(attachment.metadata)
        ? attachment.metadata
        : {},
      safeUrl
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

export function videoMimeTypeSupported(value) {
  const mimeType = typeof value === 'string'
    ? value.split(';', 1)[0].trim().toLowerCase()
    : ''
  if (!mimeType.startsWith('video/')) return false
  const video = globalThis.document?.createElement?.('video')
  return typeof video?.canPlayType === 'function' && video.canPlayType(mimeType) !== ''
}

export function attachmentIcon(attachment) {
  if (attachment?.kind === 'image') return 'image'
  if (attachment?.kind === 'audio') return 'audio_file'
  if (attachment?.kind === 'video') return 'video_file'
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
  const duration = ['audio', 'video'].includes(attachment?.kind)
    ? formatAttachmentDuration(attachment.durationMs ?? attachment.metadata?.durationMs)
    : null
  return [dimensions, duration, formatAttachmentSize(attachment?.sizeBytes), attachment?.mimeType]
    .filter(Boolean)
    .join(' · ')
}
