import { api } from 'src/boot/axios'
import { safeAttachmentUrl } from 'src/utils/attachments'

export const MAX_ATTACHMENT_DRAFTS = 8
export const MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
export const MAX_ATTACHMENT_DRAFT_BYTES = 256 * 1024 * 1024

export function normalizedMimeType(file) {
  return String(file?.type || '').split(';', 1)[0].trim().toLowerCase()
}

export function releaseAttachmentDrafts(attachments = []) {
  for (const attachment of attachments) {
    if (attachment?.objectUrl) globalThis.URL?.revokeObjectURL?.(attachment.objectUrl)
  }
}

export function attachmentKind(file) {
  const mimeType = normalizedMimeType(file)
  if (mimeType.startsWith('image/')) return 'image'
  if (mimeType.startsWith('audio/')) return 'audio'
  if (mimeType.startsWith('video/')) return 'video'
  return 'file'
}

export function attachmentPreviewUrl(file) {
  if (!['image', 'audio'].includes(attachmentKind(file)) || typeof globalThis.URL?.createObjectURL !== 'function') {
    return null
  }
  try {
    return globalThis.URL.createObjectURL(file)
  } catch {
    return null
  }
}

export async function downloadMessageAttachmentFiles(message) {
  return Promise.all((message?.attachments || []).map(async (attachment) => {
    const url = safeAttachmentUrl(attachment.url)
    if (!url) throw new Error('Invalid internal attachment URL')
    const { data } = await api.get(url, {
      baseURL: '',
      responseType: 'blob',
      suppressErrorToast: true
    })
    const type = attachment.mimeType || data?.type || 'application/octet-stream'
    return new File([data], attachment.filename || 'datei', { type })
  }))
}

export function buildAttachmentFormData(payload, attachments) {
  const formData = new FormData()
  const voiceAttachmentIndices = attachments.flatMap((attachment, index) => (
    attachment.voiceMessage === true ? [index] : []
  ))
  formData.append('payload', JSON.stringify({ ...payload, voiceAttachmentIndices }))
  for (const attachment of attachments) {
    formData.append('attachments', attachment.file, attachment.file.name || attachment.name || 'datei')
  }
  return formData
}
