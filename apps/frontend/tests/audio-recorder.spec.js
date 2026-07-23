import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createAudioContext,
  createAudioRecorder,
  encodePcmWav
} from '../src/composables/audio-recorder'

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function audioMocks(sampleRate = 16_000) {
  const track = { stop: vi.fn() }
  const processor = {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onaudioprocess: null
  }
  const source = { channelCount: 1, connect: vi.fn(), disconnect: vi.fn() }
  const gain = { gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() }
  const context = {
    sampleRate,
    state: 'running',
    destination: {},
    resume: vi.fn().mockResolvedValue(),
    close: vi.fn().mockResolvedValue(),
    createMediaStreamSource: vi.fn(() => source),
    createScriptProcessor: vi.fn(() => processor),
    createGain: vi.fn(() => gain)
  }
  const AudioContextClass = vi.fn(() => context)
  return {
    track,
    stream: { getTracks: () => [track] },
    processor,
    context,
    AudioContextClass
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('PCM-WAV encoder', () => {
  it('writes a valid mono 16-bit WAV header and resamples to 16 kHz', () => {
    const input = new Float32Array(48_000)
    input[0] = -1
    input[1] = 1

    const wav = encodePcmWav(input, 48_000)
    const bytes = new Uint8Array(wav)
    const view = new DataView(wav)
    const text = (offset, length) => String.fromCharCode(...bytes.slice(offset, offset + length))

    expect(text(0, 4)).toBe('RIFF')
    expect(text(8, 4)).toBe('WAVE')
    expect(text(12, 4)).toBe('fmt ')
    expect(text(36, 4)).toBe('data')
    expect(view.getUint32(4, true) + 8).toBe(wav.byteLength)
    expect(view.getUint16(20, true)).toBe(1)
    expect(view.getUint16(22, true)).toBe(1)
    expect(view.getUint32(24, true)).toBe(16_000)
    expect(view.getUint16(34, true)).toBe(16)
    expect(view.getUint32(40, true)).toBe(16_000 * 2)
  })

  it('rejects empty recordings', () => {
    expect(() => encodePcmWav(new Float32Array(), 48_000)).toThrow('recorder.empty')
  })
})

describe('audio recorder lifecycle', () => {
  it('falls back without a fixed sample rate only for NotSupportedError', () => {
    const contexts = []
    class AudioContextMock {
      constructor(options) {
        contexts.push(options)
        if (options?.sampleRate) {
          const error = new Error('unsupported')
          error.name = 'NotSupportedError'
          throw error
        }
      }
    }

    expect(createAudioContext(AudioContextMock)).toBeInstanceOf(AudioContextMock)
    expect(contexts).toEqual([{ sampleRate: 16_000 }, undefined])
  })

  it('blocks concurrent starts and stops a stream that arrives after cancellation', async () => {
    const pendingStream = deferred()
    const mocks = audioMocks()
    const getUserMedia = vi.fn(() => pendingStream.promise)
    const recorder = createAudioRecorder({
      mediaDevices: { getUserMedia },
      AudioContextClass: mocks.AudioContextClass
    })

    const firstStart = recorder.startRecording()
    const secondStart = recorder.startRecording()
    expect(recorder.starting.value).toBe(true)
    await expect(secondStart).resolves.toBe(false)
    expect(getUserMedia).toHaveBeenCalledTimes(1)

    await recorder.cancelRecording()
    pendingStream.resolve(mocks.stream)
    await expect(firstStart).resolves.toBe(false)

    expect(mocks.track.stop).toHaveBeenCalled()
    expect(recorder.recording.value).toBe(false)
  })

  it('stops automatically at the duration limit and releases all resources', async () => {
    vi.useFakeTimers()
    let now = 0
    const mocks = audioMocks(16_000)
    const recorder = createAudioRecorder({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(mocks.stream) },
      AudioContextClass: mocks.AudioContextClass,
      now: () => now,
      maxDurationMs: 1000
    })
    await recorder.startRecording()
    mocks.processor.onaudioprocess({
      inputBuffer: {
        numberOfChannels: 1,
        length: 16,
        getChannelData: () => new Float32Array(16).fill(0.25)
      }
    })

    now = 1000
    await vi.advanceTimersByTimeAsync(1000)
    await vi.waitFor(() => expect(recorder.autoStoppedFile.value).not.toBeNull())

    expect(recorder.limitReached.value).toBe(true)
    expect(recorder.recording.value).toBe(false)
    expect(mocks.track.stop).toHaveBeenCalled()
    expect(mocks.context.close).toHaveBeenCalled()
    expect(mocks.processor.disconnect).toHaveBeenCalled()
  })
})
