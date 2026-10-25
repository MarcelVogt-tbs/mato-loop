import { formatByteSize } from 'src/utils/resource-metrics'
import { absoluteComputerPath } from 'src/features/files/state'

const ATTACHMENT_KINDS = new Set(['image', 'audio', 'video', 'text', 'document', 'file'])
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
const INTERNAL_ATTACHMENT_URL = new RegExp(
  `^/api/sessions/${UUID}/messages/${UUID}/attachments/${UUID}$`,
  'i'
)
const INTERNAL_FILE_DOWNLOAD_PATH = '/api/workspace/files/download'
const INTERNAL_FILE_CONTENT_PATH = '/api/workspace/files/content'
const PROVIDED_IMAGE_MAX_BYTES = 5 * 1024 * 1024
const PROVIDED_IMAGE_MIME_TYPES = new Map([
  ['.gif', 'image/gif'],
  ['.jpeg', 'image/jpeg'],
  ['.jpg', 'image/jpeg'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.webp', 'image/webp']
])
const PROVIDED_MEDIA_MIME_TYPES = new Map([
  ['.pdf', 'application/pdf'],
  ['.mp3', 'audio/mpeg'], ['.wav', 'audio/wav'], ['.ogg', 'audio/ogg'], ['.oga', 'audio/ogg'],
  ['.m4a', 'audio/mp4'], ['.aac', 'audio/aac'], ['.flac', 'audio/flac'], ['.opus', 'audio/ogg'],
  ['.mp4', 'video/mp4'], ['.webm', 'video/webm'], ['.mov', 'video/quicktime'],
  ['.stl', 'model/stl'], ['.off', 'model/vnd.off']
])
const UUID_PATTERN = new RegExp(`^${UUID}$`, 'i')
const PROVIDED_FILE_DOWNLOAD_URL = new RegExp(
  `^/api/workspace/files/provided/(${UUID})/(${UUID})/(0|[1-9][0-9]*)/download$`, 'i'
)
const FILE_EXTENSION_ICONS = {
  pdf: 'picture_as_pdf',
  doc: 'article', docx: 'article', odt: 'article', rtf: 'article',
  xls: 'table_view', xlsx: 'table_view', ods: 'table_view', csv: 'table_view', tsv: 'table_view',
  ppt: 'slideshow', pptx: 'slideshow', odp: 'slideshow',
  zip: 'folder_zip', rar: 'folder_zip', '7z': 'folder_zip', tar: 'folder_zip',
  gz: 'folder_zip', tgz: 'folder_zip', bz2: 'folder_zip', xz: 'folder_zip',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', svg: 'image',
  mp3: 'audio_file', wav: 'audio_file', ogg: 'audio_file', flac: 'audio_file', m4a: 'audio_file',
  mp4: 'video_file', webm: 'video_file', mov: 'video_file', avi: 'video_file', mkv: 'video_file',
  txt: 'description', md: 'description', log: 'description',
  js: 'code', ts: 'code', py: 'code', html: 'code', css: 'code',
  json: 'code', xml: 'code', yaml: 'code', yml: 'code', sh: 'code', sql: 'code',
  stl: 'view_in_ar', off: 'view_in_ar'
}

export function validRelativePath(value, allowRoot = false) {
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

function safeFileUrl(value, expectedPath) {
  if (typeof value !== 'string' || !value.startsWith(`${expectedPath}?`)) return null
  let parsed
  try {
    parsed = new URL(value, 'https://mato-loop.invalid')
  } catch {
    return null
  }
  const keys = [...parsed.searchParams.keys()]
  const scope = parsed.searchParams.get('scope')
  const allowedKeys = scope === 'session'
    ? new Set(['path', 'scope', 'session_id', 'root_identity'])
    : scope === 'filesystem'
      ? new Set([
        'path',
        'scope',
        'session_id',
        'execution_agent_id',
        'current_directory'
      ])
      : null
  if (
    parsed.origin !== 'https://mato-loop.invalid'
    || parsed.pathname !== expectedPath
    || parsed.hash
    || !allowedKeys
    || keys.length !== allowedKeys.size
    || new Set(keys).size !== keys.length
    || keys.some((key) => !allowedKeys.has(key))
  ) return null
  const path = parsed.searchParams.get('path')
  const sessionId = parsed.searchParams.get('session_id')
  const rootIdentity = parsed.searchParams.get('root_identity')
  if (!UUID_PATTERN.test(sessionId || '')) return null
  if (scope === 'session') return validRelativePath(path) && UUID_PATTERN.test(rootIdentity || '') ? value : null
  const executionAgentId = parsed.searchParams.get('execution_agent_id')
  const currentDirectory = parsed.searchParams.get('current_directory')
  return UUID_PATTERN.test(executionAgentId || '')
    && absoluteComputerPath(path)
    && absoluteComputerPath(currentDirectory)
    ? value
    : null
}

export function safeFileDownloadUrl(value) {
  if (typeof value === 'string' && PROVIDED_FILE_DOWNLOAD_URL.test(value)) return value
  return safeFileUrl(value, INTERNAL_FILE_DOWNLOAD_PATH)
}

function safeFileContentUrl(value) {
  if (typeof value === 'string' && value.endsWith('/content') && PROVIDED_FILE_DOWNLOAD_URL.test(value.replace(/content$/, 'download'))) return value
  return safeFileUrl(value, INTERNAL_FILE_CONTENT_PATH)
}

export function toolFileAttachment(
  tool,
  result,
  maxImagePreviewBytes = PROVIDED_IMAGE_MAX_BYTES
) {
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
  const suffix = filename.match(/\.[^.]+$/u)?.[0].toLowerCase() || ''
  const previewLimit = suffix === '.svg'
    ? Math.min(maxImagePreviewBytes, PROVIDED_IMAGE_MAX_BYTES)
    : maxImagePreviewBytes
  const imageMimeType = size <= previewLimit
    ? PROVIDED_IMAGE_MIME_TYPES.get(suffix)
    : null
  return {
    id: `provided-${result.id || tool.id || path}`,
    kind: imageMimeType ? 'image' : 'file',
    mimeType: imageMimeType || 'application/octet-stream',
    sizeBytes: size,
    filename,
    url: imageMimeType
      ? safeUrl.replace(INTERNAL_FILE_DOWNLOAD_PATH, INTERNAL_FILE_CONTENT_PATH)
      : safeUrl,
    fileRootId: params.get('root_identity'),
    filePath: path
  }
}

function attachmentFileTarget(attachment) {
  const safeUrl = safeFileDownloadUrl(attachment?.url) || safeFileContentUrl(attachment?.url)
  if (safeUrl) {
    const params = new URL(safeUrl, 'https://mato-loop.invalid').searchParams
    return {
      rootIdentity: params.get('root_identity') || params.get('execution_agent_id'),
      path: params.get('path')
    }
  }
  const rootIdentity = typeof attachment?.fileRootId === 'string' ? attachment.fileRootId : ''
  const path = typeof attachment?.filePath === 'string' ? attachment.filePath : ''
  return UUID_PATTERN.test(rootIdentity) && validRelativePath(path)
    ? { rootIdentity, path }
    : null
}

export function managedSessionFileTarget(attachment, sessionId) {
  const size = Number(attachment?.sizeBytes)
  const target = attachmentFileTarget(attachment)
  const fileRevision = Number(attachment?.fileRevision)
  const filename = target?.path.split('/').at(-1) || ''
  if (
    !UUID_PATTERN.test(sessionId || '')
    || !UUID_PATTERN.test(attachment?.fileId || '')
    || !Number.isSafeInteger(fileRevision)
    || fileRevision < 1
    || !target
    || !filename
    || !Number.isSafeInteger(size)
    || size < 0
  ) return null
  return {
    root: 'session',
    ...target,
    filename,
    sizeBytes: size,
    sessionId,
    fileRevision
  }
}

export function toolFileActions(result) {
  if (!Array.isArray(result?.fileActions)) return []
  return result.fileActions.flatMap((action) => {
    if (!action || typeof action !== 'object' || action.presentation === 'internal') return []
    if (action.intent === 'draft' || action.draftId || action.draftOperation) return []
    const safeUrl = safeFileDownloadUrl(action.downloadUrl)
    const size = Number(action.sizeBytes)
    const path = typeof action.path === 'string' ? action.path : ''
    const filename = typeof action.filename === 'string' ? action.filename : ''
    const rootIdentity = typeof action.rootIdentity === 'string' ? action.rootIdentity : ''
    const scope = action.root === 'session' ? 'session' : action.root === 'workspace' ? 'filesystem' : null
    const executionAgentId = typeof action.executionAgentId === 'string' ? action.executionAgentId : ''
    const currentDirectory = typeof action.currentDirectory === 'string' ? action.currentDirectory : ''
    const provided = action.operation === 'provide' && safeUrl?.match(PROVIDED_FILE_DOWNLOAD_URL)
    const kind = action.kind || 'file'
    if (
      !safeUrl
      || !scope
      || !['write', 'create', 'edit', 'delete', 'mkdir', 'copy', 'move', 'provide'].includes(action.operation)
      || !['file', 'directory', 'collection', 'path'].includes(kind)
      || (action.sizeBytes == null && !action.kind)
      || (action.sizeBytes != null && (!Number.isSafeInteger(size) || size < 0))
      || (filename !== path.split('/').at(-1) && !(provided && (
        (kind === 'collection' && filename === 'files.zip') || (kind === 'directory' && ['.', '/'].includes(path) && filename === 'files')
      )))
      || filename.length > 255
    ) return []
    const params = new URL(safeUrl, 'https://mato-loop.invalid').searchParams
    const sessionId = provided ? provided[1] : params.get('session_id')
    if (
      (!provided && (params.get('scope') !== scope || params.get('path') !== path))
      || (scope === 'session' && (!UUID_PATTERN.test(rootIdentity) || !validRelativePath(path, provided && kind === 'directory')
        || (!provided && params.get('root_identity') !== rootIdentity)))
      || !sessionId
      || (scope === 'filesystem' && (
        !UUID_PATTERN.test(executionAgentId) || !path.startsWith('/') || !currentDirectory.startsWith('/')
        || (!provided && (params.get('execution_agent_id') !== executionAgentId || params.get('current_directory') !== currentDirectory))
        || rootIdentity
      ))
      || (scope === 'session' && [
        'executionAgentId', 'currentDirectory'
      ].some((key) => Object.hasOwn(action, key)))
    ) return []
    return [{
      operation: action.operation,
      ...(action.kind ? { kind } : {}),
      root: action.root,
      ...(rootIdentity ? { rootIdentity } : {}),
      path,
      filename,
      sizeBytes: action.sizeBytes == null ? null : size,
      sessionId,
      ...(typeof action.sourcePath === 'string' ? { sourcePath: action.sourcePath, sourceRoot: action.sourceRoot } : {}),
      ...(provided ? {
        targetUrl: safeUrl.replace(/download$/, 'target'),
        fileId: UUID_PATTERN.test(action.fileId || '') ? action.fileId : null,
        fileRevision: Number.isSafeInteger(action.fileRevision) && action.fileRevision > 0 ? action.fileRevision : null
      } : {}),
      ...(scope === 'filesystem'
        ? { executionAgentId, currentDirectory }
        : {}),
      downloadUrl: safeUrl
    }]
  })
}

export function toolOpenFileActions(tool, result) {
  const actions = toolFileActions(result)
  if (tool?.name !== 'provide_file') return actions
  for (const attachment of result?.attachments || []) {
    const target = managedSessionFileTarget(attachment, attachment?.fileRootId)
    if (!target) continue
    actions.push({
      operation: 'provide',
      root: 'session',
      ...target
    })
  }
  return [...new Map(actions.map((action) => [
    `${action.rootIdentity || action.executionAgentId}:${action.path}`,
    action
  ])).values()]
}

export function providedFileAttachments(actions, { maxImagePreviewBytes = PROVIDED_IMAGE_MAX_BYTES, maxUploadBytes = 64 * 1024 * 1024 } = {}) {
  return toolFileActions({ fileActions: actions }).filter((action) => action.operation === 'provide').map((action) => {
    const archive = ['directory', 'collection'].includes(action.kind)
    const filename = action.kind === 'directory' ? `${action.filename}.zip` : action.filename
    const suffix = filename.match(/\.[^.]+$/u)?.[0].toLowerCase() || ''
    const mimeType = archive ? 'application/zip' : PROVIDED_IMAGE_MIME_TYPES.get(suffix) || PROVIDED_MEDIA_MIME_TYPES.get(suffix)
    const limit = mimeType?.startsWith('image/')
      ? suffix === '.svg' ? Math.min(maxImagePreviewBytes, PROVIDED_IMAGE_MAX_BYTES) : maxImagePreviewBytes
      : mimeType?.startsWith('model/') ? 64 * 1024 * 1024 : Math.min(maxUploadBytes, 64 * 1024 * 1024)
    const preview = Boolean(!archive && mimeType && action.sizeBytes != null && action.sizeBytes <= limit)
    return {
      id: action.downloadUrl,
      kind: preview && mimeType.startsWith('image/') ? 'image'
        : preview && mimeType.startsWith('audio/') ? 'audio'
          : preview && mimeType.startsWith('video/') ? 'video' : 'file',
      filename,
      mimeType: mimeType || 'application/octet-stream',
      sizeBytes: action.sizeBytes,
      url: preview ? action.downloadUrl.replace(/download(?=\?|$)/, 'content') : action.downloadUrl,
      downloadUrl: action.downloadUrl,
      fileAction: { ...action, kind: action.kind || 'file' }
    }
  })
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
      || safeFileContentUrl(attachment.url)
      || (attachment.kind === 'file' ? safeFileDownloadUrl(attachment.url) : null)
    return [{
      ...attachment,
      mimeType,
      filename: typeof attachment.filename === 'string' ? attachment.filename : null,
      metadata: attachment.metadata && typeof attachment.metadata === 'object' && !Array.isArray(attachment.metadata)
        ? attachment.metadata
        : {},
      safeUrl,
      downloadUrl: safeAttachmentUrl(attachment.downloadUrl) || safeFileDownloadUrl(attachment.downloadUrl) || safeUrl,
      audioSources: attachment.kind === 'audio' ? [
        ...(safeUrl ? [{ src: safeUrl, type: mimeType }] : []),
        ...(safeUrl && attachment.playbackUrl === `${safeUrl}?playback=true`
          && attachment.playbackMimeType === 'audio/wav'
          ? [{ src: attachment.playbackUrl, type: 'audio/wav' }]
          : [])
      ] : []
    }]
  })
}

export function toolOutputAttachments(tool, attachments) {
  return supportedAttachments(attachments).filter((attachment) => {
    if (attachment.presentation === 'internal') return false
    if (attachment.presentation === 'result') return true
    if (['browser', 'browser_take_screenshot', 'webfetch'].includes(tool?.name)) {
      return attachment.kind !== 'image'
    }
    if (tool?.name === 'render_openscad') {
      return attachment.kind !== 'image'
        && attachment.mimeType !== 'model/vnd.off' && !/\.off$/i.test(attachment.filename || '')
    }
    return true
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
  const mimeType = String(attachment?.mimeType || '').split(';', 1)[0].trim().toLowerCase()
  const extension = String(attachment?.filename || '').match(/\.([^.]+)$/u)?.[1].toLowerCase()
  if (['model/stl', 'model/vnd.off'].includes(mimeType)) return 'view_in_ar'
  if (mimeType === 'application/pdf') return 'picture_as_pdf'
  if (mimeType === 'text/csv' || mimeType.includes('spreadsheet') || mimeType.includes('excel')) return 'table_view'
  if (mimeType.includes('presentation') || mimeType.includes('powerpoint')) return 'slideshow'
  if (/(zip|compressed|gzip|tar|rar)/u.test(mimeType)) return 'folder_zip'
  if (mimeType.includes('wordprocessing') || mimeType.includes('msword') || mimeType.includes('opendocument.text')) return 'article'
  if (Object.hasOwn(FILE_EXTENSION_ICONS, extension)) return FILE_EXTENSION_ICONS[extension]
  if (mimeType.startsWith('image/')) return 'image'
  if (mimeType.startsWith('audio/')) return 'audio_file'
  if (mimeType.startsWith('video/')) return 'video_file'
  if (attachment?.kind === 'text' || mimeType.startsWith('text/')) return 'description'
  return attachment?.kind === 'document' ? 'article' : 'insert_drive_file'
}

export function formatAttachmentSize(value) {
  return formatByteSize(value) || 'Unbekannte Größe'
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
