import { describe, expect, it } from 'vitest'

import {
  attachmentDraftIcon,
  attachmentDraftLabel,
  clipboardAttachmentFiles
} from '../src/utils/attachment-drafts'

describe('attachment drafts', () => {
  it('extracts clipboard images and gives screenshots a stable display name', () => {
    const image = new Blob(['png'], { type: 'image/png' })
    Object.defineProperty(image, 'name', { value: 'image.png', configurable: true })
    const event = {
      clipboardData: {
        items: [{ kind: 'file', getAsFile: () => image }],
        files: []
      }
    }

    const files = clipboardAttachmentFiles(event, new Date(2026, 6, 23, 14, 25, 30))

    expect(files).toHaveLength(1)
    expect(files[0].name).toBe('Screenshot-20260723142530.png')
    expect(files[0].type).toBe('image/png')
  })

  it('leaves normal text paste and named clipboard files untouched', () => {
    expect(clipboardAttachmentFiles({ clipboardData: { items: [], files: [] } })).toEqual([])
    const document = new Blob(['text'], { type: 'text/plain' })
    Object.defineProperty(document, 'name', { value: 'notizen.txt' })
    const files = clipboardAttachmentFiles({
      clipboardData: { items: [{ kind: 'file', getAsFile: () => document }], files: [] }
    })
    expect(files[0]).toBe(document)
  })

  it('provides concise labels and icons for common draft types', () => {
    expect(attachmentDraftLabel({ kind: 'file', file: { name: 'bericht.pdf' } })).toBe('PDF-Datei')
    expect(attachmentDraftIcon({ kind: 'file', mimeType: 'application/pdf' })).toBe('picture_as_pdf')
    expect(attachmentDraftIcon({ kind: 'audio' })).toBe('audio_file')
    expect(attachmentDraftLabel({ kind: 'video' })).toBe('Video')
    expect(attachmentDraftIcon({ kind: 'video' })).toBe('video_file')
  })
})
