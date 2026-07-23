function clipboardFilename(file, now) {
  if (String(file?.name || '').trim() && file.name !== 'image.png') return file
  const date = now instanceof Date ? now : new Date(now)
  const timestamp = [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
    String(date.getHours()).padStart(2, '0'),
    String(date.getMinutes()).padStart(2, '0'),
    String(date.getSeconds()).padStart(2, '0')
  ].join('')
  const filename = `Screenshot-${timestamp}.png`
  if (typeof File === 'function') {
    return new File([file], filename, { type: file.type || 'image/png', lastModified: file.lastModified })
  }
  try {
    Object.defineProperty(file, 'name', { value: filename, configurable: true })
  } catch {
    return file
  }
  return file
}

export function clipboardAttachmentFiles(event, now = new Date()) {
  const clipboard = event?.clipboardData
  if (!clipboard) return []
  const itemFiles = Array.from(clipboard.items || [])
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile?.())
    .filter(Boolean)
  const files = itemFiles.length ? itemFiles : Array.from(clipboard.files || [])
  return files.map((file) => (
    String(file.type || '').toLowerCase().startsWith('image/')
      ? clipboardFilename(file, now)
      : file
  ))
}

export function attachmentDraftLabel(attachment) {
  if (attachment?.kind === 'image') return 'Bild'
  if (attachment?.kind === 'audio') return 'Audio'
  const filename = String(attachment?.file?.name || '')
  const extension = filename.includes('.') ? filename.split('.').pop().toUpperCase() : ''
  return extension && extension.length <= 8 ? `${extension}-Datei` : 'Datei'
}

export function attachmentDraftIcon(attachment) {
  if (attachment?.kind === 'image') return 'image'
  if (attachment?.kind === 'audio') return 'audio_file'
  const mimeType = String(attachment?.mimeType || '')
  if (mimeType === 'application/pdf') return 'picture_as_pdf'
  if (mimeType.startsWith('text/')) return 'description'
  if (mimeType.includes('spreadsheet') || mimeType.includes('excel')) return 'table_view'
  return 'draft'
}
