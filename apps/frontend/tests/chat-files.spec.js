// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import ChatFiles from '../src/features/chat/components/ChatFiles.vue'
import { supportedAttachments } from '../src/utils/attachments'

vi.mock('src/features/files/components/FileViewerPanel.vue', () => ({ default: {
  props: ['image', 'model', 'pdf'],
  render() { return h('div', {
    'data-preview-url': this.image?.url || this.model?.url || this.pdf?.url,
    'data-download-url': this.image?.downloadUrl || this.model?.downloadUrl || this.pdf?.downloadUrl
  }) }
} }))

const url = '/api/sessions/550e8400-e29b-41d4-a716-446655440000/messages/550e8400-e29b-41d4-a716-446655440001/attachments/550e8400-e29b-41d4-a716-446655440002'
let app
function mount(props) {
  const root = document.createElement('div')
  app = createApp({ render: () => h(ChatFiles, props) }).use(createPinia())
  for (const name of ['QIcon', 'QDialog', 'QCard', 'QBtn']) {
    app.component(name, { render() { return h('div', this.$attrs, this.$slots.default?.()) } })
  }
  app.mount(root)
  return root
}
afterEach(() => {
  app?.unmount()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it.each(['audio/ogg', 'audio/mpeg', 'audio/webm', 'audio/mp4'])(
  'offers WAV even when %s is unsupported and keeps the original download', (mimeType) => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type) => type === 'audio/wav' ? 'probably' : '')
    const attachment = {
      id: 'audio', kind: 'audio', url, mimeType,
      playbackUrl: `${url}?playback=true`, playbackMimeType: 'audio/wav'
    }
    const root = mount({ attachments: [attachment] })
    expect([...root.querySelectorAll('audio source')].map((source) => [source.getAttribute('src'), source.type])).toEqual([
      [url, mimeType], [`${url}?playback=true`, 'audio/wav']
    ])
    expect(root.querySelector('[icon="download"]').getAttribute('href')).toBe(url)
    for (const playbackUrl of ['https://evil.example/audio.wav', `${url}?playback=true&path=secret`, url.replace('002', '003') + '?playback=true']) {
      expect(supportedAttachments([{ ...attachment, playbackUrl }])[0].audioSources).toEqual([{ src: url, type: mimeType }])
    }
  }
)

it('previews a queued SVG without document navigation and releases its URL on unmount', () => {
  const createObjectURL = vi.fn(() => 'blob:local-image')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  const root = mount({ attachments: [{
    id: 'local', kind: 'image', file: new File(['<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'], 'foto.svg', { type: 'image/svg+xml' })
  }] })
  const preview = root.querySelector('img')?.getAttribute('src')
  const navigation = root.querySelector('[target="_blank"]')
  app.unmount()
  app = null
  expect([preview, navigation, revokeObjectURL.mock.calls]).toEqual(['blob:local-image', null, [['blob:local-image']]])
})

it.each(['photo.png', 'model.stl', 'model.off', 'manual.pdf'])('opens provided %s in the shared viewer', async (filename) => {
  const sessionId = '550e8400-e29b-41d4-a716-446655440000'
  const downloadUrl = `/api/workspace/files/provided/${sessionId}/550e8400-e29b-41d4-a716-446655440001/0/download`
  const root = mount({ actions: [{
    operation: 'provide', kind: 'file', root: 'session', rootIdentity: sessionId,
    path: filename, filename, sizeBytes: 12, downloadUrl
  }] })
  root.querySelector('[icon="visibility"]').click()
  await nextTick()
  const viewer = root.querySelector('[data-preview-url]')
  expect([viewer?.getAttribute('data-preview-url'), viewer?.getAttribute('data-download-url')])
    .toEqual([downloadUrl.replace(/download$/, 'content'), downloadUrl])
})

it('opens a provided text file through the existing file target', () => {
  const sessionId = '550e8400-e29b-41d4-a716-446655440000'
  const openFile = vi.fn()
  const root = mount({ onOpenFile: openFile, actions: [{
    operation: 'provide', kind: 'file', root: 'session', rootIdentity: sessionId,
    path: 'report.md', filename: 'report.md', sizeBytes: 12,
    downloadUrl: `/api/workspace/files/provided/${sessionId}/550e8400-e29b-41d4-a716-446655440001/0/download`
  }] })
  root.querySelector('[icon="visibility"]').click()
  expect(openFile.mock.calls[0][0].path).toBe('report.md')
})
