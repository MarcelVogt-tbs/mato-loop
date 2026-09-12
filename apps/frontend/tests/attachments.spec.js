import { describe, expect, it } from 'vitest'

import {
  attachmentIcon,
  attachmentMetadata,
  audioMimeTypeSupported,
  audioAttachments,
  formatAttachmentDuration,
  imageAttachments,
  managedSessionFileTarget,
  safeAttachmentUrl,
  safeFileDownloadUrl,
  supportedAttachments,
  toolFileActions,
  toolFileAttachment,
  toolOutputAttachments,
  deduplicateFileOutputAttachments,
  videoAttachments,
  videoMimeTypeSupported
} from '../src/utils/attachments'

const url = '/api/sessions/550e8400-e29b-41d4-a716-446655440000/messages/550e8400-e29b-41d4-a716-446655440001/attachments/550e8400-e29b-41d4-a716-446655440002'
const executionAgentId = '550e8400-e29b-41d4-a716-446655440003'
const rootId = '550e8400-e29b-41d4-a716-446655440004'
const downloadUrl = `/api/workspace/files/download?path=%2Fhome%2Fuser%2Freports%2Flarge.zip&scope=filesystem&session_id=550e8400-e29b-41d4-a716-446655440000&execution_agent_id=${executionAgentId}&current_directory=%2Fhome%2Fuser`

describe('persisted attachments', () => {
  it('only permits the internal hierarchical attachment route', () => {
    expect(safeAttachmentUrl(url)).toBe(url)
    expect(safeAttachmentUrl(`https://example.com${url}`)).toBeNull()
    expect(safeAttachmentUrl('/api/sessions/../secrets')).toBeNull()
  })

  it('accepts only bound internal file-download URLs', () => {
    expect(safeFileDownloadUrl(downloadUrl)).toBe(downloadUrl)
    expect(safeFileDownloadUrl(`${downloadUrl}&current_directory=%2Fother`)).toBeNull()
    expect(safeFileDownloadUrl(downloadUrl.replace('&current_directory=%2Fhome%2Fuser', ''))).toBeNull()
    expect(safeFileDownloadUrl(downloadUrl.replace('%2Fhome%2Fuser%2Freports', '..%2Fsecret'))).toBeNull()
    expect(safeFileDownloadUrl(`https://example.com${downloadUrl}`)).toBeNull()
    const sessionUrl = '/api/workspace/files/download?path=report.pdf&scope=session&session_id=550e8400-e29b-41d4-a716-446655440000&root_identity=550e8400-e29b-41d4-a716-446655440000'
    expect(safeFileDownloadUrl(sessionUrl)).toBe(sessionUrl)
    expect(safeFileDownloadUrl(sessionUrl.replace(/root_identity=[^&]+/u, 'root_identity=550e8400-e29b-41d4-a716-446655440009'))).not.toBeNull()
    expect(safeFileDownloadUrl(sessionUrl.replace(/root_identity=[^&]+/u, 'root_identity=other'))).toBeNull()
    expect(safeFileDownloadUrl(`${sessionUrl}&execution_agent_id=${executionAgentId}`)).toBeNull()
  })

  it('turns only a valid provide_file result into a download attachment', () => {
    const result = {
      id: 'result-1',
      content: JSON.stringify({
        root: 'workspace',
        path: '/home/user/reports/large.zip',
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
      url: downloadUrl,
      fileRootId: null,
      filePath: '/home/user/reports/large.zip'
    })
    expect(toolFileAttachment({ id: 'call-1', name: 'read_file' }, result)).toBeNull()
    expect(toolFileAttachment({ id: 'call-1', name: 'provide_file' }, {
      ...result,
      content: result.content.replace('large.zip",', 'other.zip",')
    })).toBeNull()
  })

  it.each([
    ['.gif', 'image/gif'],
    ['.jpeg', 'image/jpeg'],
    ['.jpg', 'image/jpeg'],
    ['.png', 'image/png'],
    ['.svg', 'image/svg+xml'],
    ['.webp', 'image/webp']
  ])('shows a provided %s file as a chat image', (extension, mimeType) => {
    const filename = `photo${extension}`
    const imageDownloadUrl = downloadUrl.replace('reports%2Flarge.zip', `images%2F${filename}`)
    const attachment = toolFileAttachment({ id: 'call-1', name: 'provide_file' }, {
      id: 'result-1',
      content: JSON.stringify({
        root: 'workspace',
        path: `/home/user/images/${filename}`,
        filename,
        sizeBytes: 1024,
        downloadUrl: imageDownloadUrl
      })
    })
    const contentUrl = imageDownloadUrl.replace('/files/download', '/files/content')

    expect(attachment).toEqual({
      id: 'provided-result-1',
      kind: 'image',
      mimeType,
      sizeBytes: 1024,
      filename,
      url: contentUrl,
      fileRootId: null,
      filePath: `/home/user/images/${filename}`
    })
    expect(imageAttachments([attachment])[0].safeUrl).toBe(contentUrl)
  })

  it('shows a provided image from session attachments', () => {
    const sessionId = '550e8400-e29b-41d4-a716-446655440000'
    const sessionDownloadUrl = `/api/workspace/files/download?path=attachments%2Fimage.PNG&scope=session&session_id=${sessionId}&root_identity=${sessionId}`
    const attachment = toolFileAttachment({ id: 'call-1', name: 'provide_file' }, {
      id: 'result-1',
      content: JSON.stringify({
        root: 'session',
        path: 'attachments/image.PNG',
        filename: 'image.PNG',
        sizeBytes: 2048,
        downloadUrl: sessionDownloadUrl
      })
    })

    expect(attachment).toMatchObject({
      kind: 'image',
      mimeType: 'image/png',
      url: sessionDownloadUrl.replace('/files/download', '/files/content')
    })
    expect(imageAttachments([attachment])[0].safeUrl).toBe(attachment.url)
  })

  it('keeps oversized provided images as downloads', () => {
    const imageDownloadUrl = downloadUrl.replace('reports%2Flarge.zip', 'images%2Fphoto.png')

    expect(toolFileAttachment({ id: 'call-1', name: 'provide_file' }, {
      id: 'result-1',
      content: JSON.stringify({
        root: 'workspace',
        path: '/home/user/images/photo.png',
        filename: 'photo.png',
        sizeBytes: 5 * 1024 * 1024 + 1,
        downloadUrl: imageDownloadUrl
      })
    })).toEqual({
      id: 'provided-result-1',
      kind: 'file',
      mimeType: 'application/octet-stream',
      sizeBytes: 5 * 1024 * 1024 + 1,
      filename: 'photo.png',
      url: imageDownloadUrl,
      fileRootId: null,
      filePath: '/home/user/images/photo.png'
    })
  })

  it('accepts only host-bound file actions', () => {
    const action = {
      operation: 'write',
      root: 'workspace',
      path: '/home/user/reports/large.zip',
      filename: 'large.zip',
      sizeBytes: 3_000_000_000,
      executionAgentId,
      currentDirectory: '/home/user',
      downloadUrl
    }
    const expected = {
      ...action,
      sessionId: '550e8400-e29b-41d4-a716-446655440000'
    }

    expect(toolFileActions({ fileActions: [action] })).toEqual([expected])
    expect(toolFileActions({ fileActions: [{ ...action, path: '/home/user/reports/other.zip' }] })).toEqual([])
    expect(toolFileActions({ fileActions: [{ ...action, currentDirectory: '/other' }] })).toEqual([])
    expect(toolFileActions({ fileActions: [{ ...action, operation: 'delete' }] })).toEqual([])
  })

  it('accepts an ordinary relative session file action', () => {
    const sessionId = '550e8400-e29b-41d4-a716-446655440000'
    const action = {
      operation: 'write',
      root: 'session',
      rootIdentity: sessionId,
      path: 'result.md',
      filename: 'result.md',
      sizeBytes: 7,
      downloadUrl: `/api/workspace/files/download?path=result.md&scope=session&session_id=${sessionId}&root_identity=${sessionId}`
    }

    expect(toolFileActions({ fileActions: [action] })).toEqual([{ ...action, sessionId }])
    expect(toolFileActions({ fileActions: [action, action] })).toHaveLength(2)
  })

  it('derives an open target only for a newly managed session file', () => {
    const sessionId = '550e8400-e29b-41d4-a716-446655440000'
    const attachment = {
      filename: 'friendly-name.bin',
      sizeBytes: 12,
      fileRootId: '550e8400-e29b-41d4-a716-446655440009',
      fileId: '550e8400-e29b-41d4-a716-446655440008',
      fileRevision: 1,
      filePath: 'generated/generated.bin'
    }

    expect(managedSessionFileTarget(attachment, sessionId)).toEqual({
      root: 'session',
      rootIdentity: attachment.fileRootId,
      path: attachment.filePath,
      filename: 'generated.bin',
      sizeBytes: 12,
      sessionId,
      fileRevision: 1
    })
    expect(managedSessionFileTarget({ ...attachment, fileRevision: 2 }, sessionId))
      .toMatchObject({ path: attachment.filePath, fileRevision: 2 })
    expect(managedSessionFileTarget({ ...attachment, fileRevision: 0 }, sessionId)).toBeNull()
    expect(managedSessionFileTarget({ ...attachment, filePath: '../generated.bin' }, sessionId)).toBeNull()
  })

  it('removes only attachments represented by the same file action', () => {
    const attachments = [
      { id: 'same', fileRootId: rootId, filePath: 'reports/large.zip' },
      { id: 'other', fileRootId: rootId, filePath: 'reports/other.zip' },
      { id: 'managed', fileRootId: rootId, filePath: 'reports/generated.bin' }
    ]

    expect(deduplicateFileOutputAttachments(attachments, [{
      rootIdentity: rootId,
      path: 'reports/large.zip'
    }]).map((attachment) => attachment.id)).toEqual(['other', 'managed'])
    expect(deduplicateFileOutputAttachments(attachments, [])).toEqual(attachments)
  })

  it('uses the bound file URL rather than untrusted attachment metadata for deduplication', () => {
    expect(deduplicateFileOutputAttachments([{
      id: 'provided',
      fileRootId: rootId,
      filePath: '/home/user/reports/other.zip',
      url: downloadUrl
    }], [{ executionAgentId, path: '/home/user/reports/large.zip' }])).toEqual([])
  })

  it('does not expose legacy draft actions as reusable file outputs', () => {
    const sessionId = '550e8400-e29b-41d4-a716-446655440000'
    const draftId = '550e8400-e29b-41d4-a716-446655440005'
    const path = `drafts/${draftId}/plan.md`
    const action = {
      operation: 'write',
      root: 'session',
      rootIdentity: sessionId,
      path,
      filename: 'plan.md',
      sizeBytes: 7,
      intent: 'draft',
      draftId,
      revision: 1,
      draftOperation: 'create',
      representations: [],
      downloadUrl: `/api/workspace/files/download?path=${encodeURIComponent(path)}&scope=session&session_id=${sessionId}&root_identity=${sessionId}`
    }

    expect(toolFileActions({ fileActions: [action] })).toEqual([])
  })

  it('keeps supported image metadata and marks unsafe URLs', () => {
    const attachments = imageAttachments([
      { id: 'one', kind: 'image', mimeType: 'image/png', url, width: 10, height: 20, sizeBytes: 2048 },
      { id: 'two', kind: 'file', mimeType: 'text/html', url: 'https://example.com' }
    ])
    expect(attachments).toHaveLength(1)
    expect(attachments[0].safeUrl).toBe(url)
    expect(attachmentMetadata(attachments[0])).toBe('10 × 20 px · 2 KB · image/png')
    expect(supportedAttachments([{
      kind: 'image',
      mimeType: 'image/png',
      url: `https://example.com${downloadUrl.replace('/files/download', '/files/content')}`
    }])[0].safeUrl).toBeNull()
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
      { kind: 'video', mimeType: 'video/mp4', url },
      { kind: 'text', mimeType: 'text/plain', url },
      { kind: 'document', mimeType: 'application/pdf', url },
      { kind: 'file', mimeType: 'application/zip', url },
      { kind: 'archive', mimeType: 'application/zip', url }
    ])

    expect(attachments).toHaveLength(6)
    expect(attachments.map((attachment) => attachment.kind))
      .toEqual(['image', 'audio', 'video', 'text', 'document', 'file'])
    expect(attachmentIcon(attachments[2])).toBe('video_file')
    expect(attachmentIcon(attachments[3])).toBe('description')
    expect(attachmentIcon(attachments[4])).toBe('picture_as_pdf')
    expect(attachmentIcon(attachments[5])).toBe('folder_zip')
  })

  it('hides browser screenshots only from the visible tool output', () => {
    const attachments = [
      { id: 'screenshot', kind: 'image', mimeType: 'image/png', filename: 'browser.png', url },
      { id: 'renamed', kind: 'image', mimeType: 'image/png', filename: 'page.png', url },
      { id: 'file', kind: 'file', mimeType: 'application/pdf', url }
    ]
    const original = structuredClone(attachments)

    expect(toolOutputAttachments({ name: 'browser' }, attachments))
      .toEqual([expect.objectContaining({ id: 'file' })])
    expect(toolOutputAttachments({ name: 'browser_take_screenshot' }, attachments))
      .toEqual([expect.objectContaining({ id: 'file' })])
    expect(toolOutputAttachments({ name: 'other_tool' }, attachments)).toHaveLength(3)
    expect(supportedAttachments(attachments)).toHaveLength(3)
    expect(attachments).toEqual(original)
  })

  it('hides OpenSCAD previews only from the visible tool output', () => {
    const attachments = [
      { id: 'model', kind: 'file', mimeType: 'model/stl', url },
      { id: 'off', kind: 'file', mimeType: 'model/vnd.off', filename: 'preview.off', url },
      { id: 'preview', kind: 'image', mimeType: 'image/png', url }
    ]

    expect(toolOutputAttachments({ name: 'render_openscad' }, attachments))
      .toEqual([expect.objectContaining({ id: 'model' })])
    expect(toolOutputAttachments({ name: 'other_tool' }, attachments)).toHaveLength(3)
    expect(attachments).toHaveLength(3)
  })

  it('hides images fetched for agent inspection from visible tool output', () => {
    const attachments = [
      { id: 'image', kind: 'image', category: 'downloaded', mimeType: 'image/jpeg', url },
      {
        id: 'page',
        kind: 'image',
        category: 'downloaded',
        mimeType: 'image/jpeg',
        metadata: { pageNumber: 1 },
        url
      },
      { id: 'file', kind: 'file', category: 'downloaded', mimeType: 'application/pdf', url }
    ]

    expect(toolOutputAttachments({ name: 'webfetch' }, attachments))
      .toEqual([expect.objectContaining({ id: 'file' })])
    expect(toolOutputAttachments({ name: 'other_tool' }, attachments)).toHaveLength(3)
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

  it('keeps playable video metadata and rejects unsupported video MIME types', () => {
    const originalDocument = globalThis.document
    globalThis.document = {
      createElement: () => ({
        canPlayType: (mimeType) => mimeType === 'video/mp4' ? 'maybe' : ''
      })
    }
    try {
      const attachments = videoAttachments([{
        kind: 'video',
        mimeType: 'video/mp4; codecs=avc1',
        url,
        durationMs: 65_000,
        width: 1920,
        height: 1080,
        sizeBytes: 4096
      }])
      expect(attachments[0].safeUrl).toBe(url)
      expect(attachmentMetadata(attachments[0])).toBe('1920 × 1080 px · 1:05 · 4 KB · video/mp4')
      expect(videoMimeTypeSupported('video/mp4; codecs=avc1')).toBe(true)
      expect(videoMimeTypeSupported('video/unknown')).toBe(false)
      expect(videoMimeTypeSupported('text/html')).toBe(false)
    } finally {
      if (originalDocument === undefined) delete globalThis.document
      else globalThis.document = originalDocument
    }
  })
})
