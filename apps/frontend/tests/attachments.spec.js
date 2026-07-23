import { describe, expect, it } from 'vitest'

import { attachmentMetadata, imageAttachments, safeAttachmentUrl } from '../src/utils/attachments'

const url = '/api/sessions/550e8400-e29b-41d4-a716-446655440000/messages/550e8400-e29b-41d4-a716-446655440001/attachments/550e8400-e29b-41d4-a716-446655440002'

describe('tool result attachments', () => {
  it('only permits the internal hierarchical attachment route', () => {
    expect(safeAttachmentUrl(url)).toBe(url)
    expect(safeAttachmentUrl(`https://example.com${url}`)).toBeNull()
    expect(safeAttachmentUrl('/api/sessions/../secrets')).toBeNull()
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
})
