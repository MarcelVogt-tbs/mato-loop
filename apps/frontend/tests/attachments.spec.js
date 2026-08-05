import { describe, expect, it } from 'vitest'

import {
  attachmentIcon,
  attachmentMetadata,
  audioMimeTypeSupported,
  audioAttachments,
  formatAttachmentDuration,
  imageAttachments,
  safeAttachmentUrl,
  safeFileDownloadUrl,
  supportedAttachments,
  toolFileActions,
  toolFileAttachment
} from '../src/utils/attachments'

const url = '/api/sessions/550e8400-e29b-41d4-a716-446655440000/messages/550e8400-e29b-41d4-a716-446655440001/attachments/550e8400-e29b-41d4-a716-446655440002'
const downloadUrl = '/api/workspace/files/download?path=reports%2Flarge.zip&scope=filesystem&session_id=550e8400-e29b-41d4-a716-446655440000&root_identity=original-workspace'

describe('persisted attachments', () => {
  it('only permits the internal hierarchical attachment route', () => {
    expect(safeAttachmentUrl(url)).toBe(url)
    expect(safeAttachmentUrl(`https://example.com${url}`)).toBeNull()
    expect(safeAttachmentUrl('/api/sessions/../secrets')).toBeNull()
  })

  it('accepts only bound internal file-download URLs', () => {
    expect(safeFileDownloadUrl(downloadUrl)).toBe(downloadUrl)
    expect(safeFileDownloadUrl(`${downloadUrl}&working_directory=other`)).toBeNull()
    expect(safeFileDownloadUrl(downloadUrl.replace('reports%2Flarge.zip', '..%2Fsecret'))).toBeNull()
    expect(safeFileDownloadUrl(`https://example.com${downloadUrl}`)).toBeNull()
    const sessionUrl = '/api/workspace/files/download?path=files%2Freport.pdf&scope=session&session_id=550e8400-e29b-41d4-a716-446655440000&root_identity=550e8400-e29b-41d4-a716-446655440000'
    expect(safeFileDownloadUrl(sessionUrl)).toBe(sessionUrl)
    expect(safeFileDownloadUrl(sessionUrl.replace(/root_identity=[^&]+/u, 'root_identity=other'))).toBeNull()
  })

  it('turns only a valid provide_file result into a download attachment', () => {
    const result = {
      id: 'result-1',
      content: JSON.stringify({
        root: 'workspace',
        path: 'reports/large.zip',
        filename: 'large.zip',
        sizeBytes: 3_000_000_000,
        downloadUrl
      })
    }

    expect(toolFileAttachment({ id: 'call-1', name: 'provide_file' }, result)).toEqual({
      id: 'provided-result-1',
      kind: 'file',
      mimeType: 'application/octet-stream',
      sizeBytes: 3_000_000_000,
      filename: 'large.zip',
      url: downloadUrl
    })
    expect(toolFileAttachment({ id: 'call-1', name: 'read_file' }, result)).toBeNull()
    expect(toolFileAttachment({ id: 'call-1', name: 'provide_file' }, {
      ...result,
      content: result.content.replace('large.zip",', 'other.zip",')
    })).toBeNull()
  })

  it('accepts only host-bound file actions', () => {
    const action = {
      operation: 'write',
      root: 'workspace',
      rootIdentity: 'original-workspace',
      path: 'reports/large.zip',
      filename: 'large.zip',
      sizeBytes: 3_000_000_000,
      downloadUrl
    }
    const expected = {
      ...action,
      sessionId: '550e8400-e29b-41d4-a716-446655440000'
    }

    expect(toolFileActions({ fileActions: [action] })).toEqual([expected])
    expect(toolFileActions({ fileActions: [{ ...action, path: 'reports/other.zip' }] })).toEqual([])
    expect(toolFileActions({ fileActions: [{ ...action, rootIdentity: 'other' }] })).toEqual([])
    expect(toolFileActions({ fileActions: [{ ...action, operation: 'delete' }] })).toEqual([])
  })

  it('keeps supported image metadata and marks unsafe URLs', () => {
    const attachments = imageAttachments([
      { id: 'one', kind: 'image', mimeType: 'image/png', url, width: 10, height: 20, sizeBytes: 2048 },
      { id: 'two', kind: 'file', mimeType: 'text/html', url: 'https://example.com' }
    ])
    expect(attachments).toHaveLength(1)
    expect(attachments[0].safeUrl).toBe(url)
    expect(attachmentMetadata(attachments[0])).toBe('10 × 20 px · 2 KB · image/png')
  })

  it('keeps arbitrary audio formats with robust duration and metadata handling', () => {
    const attachments = audioAttachments([
      {
        id: 'audio-one',
        kind: 'audio',
        mimeType: 'audio/x-wav; charset=binary',
        url,
        durationMs: 65_400,
        sizeBytes: 4096,
        metadata: null
      },
      { id: 'audio-two', kind: 'audio', mimeType: 'audio/mpeg', url }
    ])

    expect(attachments).toHaveLength(2)
    expect(attachments[0]).toMatchObject({ mimeType: 'audio/x-wav', metadata: {}, safeUrl: url })
    expect(attachmentMetadata(attachments[0])).toBe('1:05 · 4 KB · audio/x-wav')
    expect(formatAttachmentDuration(3_661_000)).toBe('1:01:01')
  })

  it('returns every persisted attachment kind and rejects unknown kinds', () => {
    const attachments = supportedAttachments([
      { kind: 'image', mimeType: 'image/png', url },
      { kind: 'audio', mimeType: 'audio/mpeg', url },
      { kind: 'text', mimeType: 'text/plain', url },
      { kind: 'document', mimeType: 'application/pdf', url },
      { kind: 'file', mimeType: 'application/zip', url },
      { kind: 'archive', mimeType: 'application/zip', url }
    ])

    expect(attachments).toHaveLength(5)
    expect(attachments.map((attachment) => attachment.kind))
      .toEqual(['image', 'audio', 'text', 'document', 'file'])
    expect(attachmentIcon(attachments[2])).toBe('description')
    expect(attachmentIcon(attachments[3])).toBe('picture_as_pdf')
    expect(attachmentIcon(attachments[4])).toBe('folder_zip')
  })

  it('only enables the audio player when the browser accepts the MIME type', () => {
    const originalDocument = globalThis.document
    globalThis.document = {
      createElement: () => ({
        canPlayType: (mimeType) => mimeType === 'audio/ogg' ? 'probably' : ''
      })
    }
    try {
      expect(audioMimeTypeSupported('audio/ogg; codecs=opus')).toBe(true)
      expect(audioMimeTypeSupported('audio/unknown')).toBe(false)
      expect(audioMimeTypeSupported('application/ogg')).toBe(false)
    } finally {
      if (originalDocument === undefined) delete globalThis.document
      else globalThis.document = originalDocument
    }
  })
})
