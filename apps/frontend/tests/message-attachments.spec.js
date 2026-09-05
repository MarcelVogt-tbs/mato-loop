// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import MessageAttachments from '../src/components/MessageAttachments.vue'
import { supportedAttachments } from '../src/utils/attachments'

vi.mock('components/FileViewerPanel.vue', () => ({ default: { render: () => null } }))

const url = '/api/sessions/550e8400-e29b-41d4-a716-446655440000/messages/550e8400-e29b-41d4-a716-446655440001/attachments/550e8400-e29b-41d4-a716-446655440002'
let app
afterEach(() => {
  app?.unmount()
  vi.restoreAllMocks()
})

it.each(['audio/ogg', 'audio/mpeg', 'audio/webm', 'audio/mp4'])(
  'offers WAV even when %s is unsupported and keeps the original download', (mimeType) => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type) => type === 'audio/wav' ? 'probably' : '')
    const attachment = {
      id: 'audio', kind: 'audio', url, mimeType,
      playbackUrl: `${url}?playback=true`, playbackMimeType: 'audio/wav'
    }
    const root = document.createElement('div')
    app = createApp({ render: () => h(MessageAttachments, { attachments: [attachment] }) })
    for (const name of ['QIcon', 'QTooltip', 'QBanner', 'QDialog', 'QCard', 'QBtn']) {
      app.component(name, { render() { return h('div', this.$attrs, this.$slots.default?.()) } })
    }
    app.mount(root)
    expect([...root.querySelectorAll('audio source')].map((source) => [source.getAttribute('src'), source.type])).toEqual([
      [url, mimeType], [`${url}?playback=true`, 'audio/wav']
    ])
    expect(root.querySelector('[aria-label="Audio herunterladen"]').getAttribute('href')).toBe(url)
    for (const playbackUrl of ['https://evil.example/audio.wav', `${url}?playback=true&path=secret`, url.replace('002', '003') + '?playback=true']) {
      expect(supportedAttachments([{ ...attachment, playbackUrl }])[0].audioSources).toEqual([{ src: url, type: mimeType }])
    }
  }
)
