import { onBeforeUnmount, ref } from 'vue'
import { PWA_BEFORE_UPDATE_EVENT } from '../utils/pwa-update'

const TARGET_SAMPLE_RATE = 16_000
export const MAX_RECORDING_DURATION_MS = 5 * 60 * 1000

function resample(samples, inputSampleRate, outputSampleRate) {
  if (inputSampleRate === outputSampleRate) return samples
  const outputLength = Math.max(1, Math.round(samples.length * outputSampleRate / inputSampleRate))
  const output = new Float32Array(outputLength)
  const ratio = inputSampleRate / outputSampleRate
  for (let index = 0; index < outputLength; index += 1) {
    const position = index * ratio
    const left = Math.floor(position)
    const right = Math.min(left + 1, samples.length - 1)
    const fraction = position - left
    output[index] = samples[left] + (samples[right] - samples[left]) * fraction
  }
  return output
}

export function encodePcmWav(samples, inputSampleRate, outputSampleRate = TARGET_SAMPLE_RATE) {
  if (!(samples instanceof Float32Array) || samples.length === 0) {
    throw new Error('recorder.empty')
  }
  if (!Number.isFinite(inputSampleRate) || inputSampleRate <= 0) {
    throw new Error('recorder.invalidSampleRate')
  }
  const pcm = resample(samples, inputSampleRate, outputSampleRate)
  const buffer = new ArrayBuffer(44 + pcm.length * 2)
  const view = new DataView(buffer)
  const writeText = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index))
    }
  }
  writeText(0, 'RIFF')
  view.setUint32(4, 36 + pcm.length * 2, true)
  writeText(8, 'WAVE')
  writeText(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, outputSampleRate, true)
  view.setUint32(28, outputSampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeText(36, 'data')
  view.setUint32(40, pcm.length * 2, true)
  for (let index = 0; index < pcm.length; index += 1) {
    const value = Math.max(-1, Math.min(1, pcm[index]))
    view.setInt16(44 + index * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true)
  }
  return buffer
}

export function createAudioContext(AudioContextClass) {
  try {
    return new AudioContextClass({ sampleRate: TARGET_SAMPLE_RATE })
  } catch (error) {
    if (error?.name !== 'NotSupportedError') throw error
    return new AudioContextClass()
  }
}

export function recorderErrorMessage(error) {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    return 'Dieser Browser unterstützt keine Mikrofonaufnahme.'
  }
  if (['NotAllowedError', 'SecurityError'].includes(error?.name)) {
    return 'Der Mikrofonzugriff wurde nicht erlaubt. Prüfe die Browserberechtigung.'
  }
  if (['NotFoundError', 'DevicesNotFoundError'].includes(error?.name)) {
    return 'Es wurde kein verfügbares Mikrofon gefunden.'
  }
  if (['NotReadableError', 'TrackStartError'].includes(error?.name)) {
    return 'Das Mikrofon wird bereits verwendet oder konnte nicht geöffnet werden.'
  }
  return 'Die Mikrofonaufnahme konnte nicht gestartet werden.'
}

export function createAudioRecorder({
  mediaDevices = globalThis.navigator?.mediaDevices,
  AudioContextClass = globalThis.window?.AudioContext || globalThis.window?.webkitAudioContext,
  now = () => performance.now(),
  maxDurationMs = MAX_RECORDING_DURATION_MS,
  onAutoStopped = null,
  setTimer = globalThis.setInterval,
  clearTimer = globalThis.clearInterval
} = {}) {
  const starting = ref(false)
  const recording = ref(false)
  const finalizing = ref(false)
  const durationMs = ref(0)
  const limitReached = ref(false)
  const autoStoppedFile = ref(null)
  let generation = 0
  let disposed = false
  let active = null
  let stoppingPromise = null

  async function cleanup(resources) {
    if (!resources) return
    clearTimer(resources.durationTimer)
    resources.durationTimer = null
    if (resources.processor) resources.processor.onaudioprocess = null
    for (const node of [resources.processor, resources.source, resources.silentGain]) {
      try {
        node?.disconnect()
      } catch {
        // Already disconnected Web Audio nodes need no further handling.
      }
    }
    resources.stream?.getTracks().forEach((track) => track.stop())
    if (resources.context && resources.context.state !== 'closed') {
      await resources.context.close().catch(() => {})
    }
  }

  function isCurrent(resources, ownGeneration) {
    return !disposed && active === resources && generation === ownGeneration
  }

  async function stopRecording() {
    if (stoppingPromise) return stoppingPromise
    const resources = active
    if (!recording.value || !resources?.context) return null
    finalizing.value = true
    recording.value = false
    starting.value = false
    generation += 1
    active = null
    durationMs.value = Math.min(maxDurationMs, now() - resources.startedAt)

    stoppingPromise = (async () => {
      const length = resources.chunks.reduce((total, chunk) => total + chunk.length, 0)
      const samples = new Float32Array(length)
      let offset = 0
      for (const chunk of resources.chunks) {
        samples.set(chunk, offset)
        offset += chunk.length
      }
      await cleanup(resources)
      resources.chunks = []
      const wav = encodePcmWav(samples, resources.context.sampleRate)
      return new File([wav], `sprachnachricht-${new Date().toISOString().replace(/[:.]/g, '-')}.wav`, {
        type: 'audio/wav'
      })
    })()

    try {
      return await stoppingPromise
    } finally {
      stoppingPromise = null
      finalizing.value = false
    }
  }

  function stopAtLimit(resources) {
    if (active !== resources || !recording.value || limitReached.value) return
    limitReached.value = true
    durationMs.value = maxDurationMs
    void stopRecording().then((file) => {
      if (file) onAutoStopped?.(file)
      if (!disposed && file && !onAutoStopped) autoStoppedFile.value = file
    }).catch(() => {})
  }

  async function startRecording() {
    if (disposed || starting.value || recording.value || stoppingPromise) return false
    if (!mediaDevices?.getUserMedia || !AudioContextClass) throw new Error('recorder.unsupported')

    starting.value = true
    durationMs.value = 0
    limitReached.value = false
    autoStoppedFile.value = null
    const ownGeneration = ++generation
    const resources = {
      stream: null,
      context: null,
      source: null,
      processor: null,
      silentGain: null,
      durationTimer: null,
      startedAt: 0,
      chunks: [],
      sampleCount: 0,
      maxSamples: 0
    }
    active = resources

    try {
      resources.stream = await mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      })
      if (!isCurrent(resources, ownGeneration)) {
        await cleanup(resources)
        return false
      }

      resources.context = createAudioContext(AudioContextClass)
      await resources.context.resume()
      if (!isCurrent(resources, ownGeneration)) {
        await cleanup(resources)
        return false
      }

      resources.source = resources.context.createMediaStreamSource(resources.stream)
      resources.processor = resources.context.createScriptProcessor(4096, resources.source.channelCount || 1, 1)
      resources.silentGain = resources.context.createGain()
      resources.silentGain.gain.value = 0
      resources.maxSamples = Math.floor(resources.context.sampleRate * maxDurationMs / 1000)
      resources.processor.onaudioprocess = (event) => {
        if (active !== resources || !recording.value) return
        const remaining = resources.maxSamples - resources.sampleCount
        if (remaining <= 0) {
          stopAtLimit(resources)
          return
        }
        const channelCount = event.inputBuffer.numberOfChannels
        const sampleCount = Math.min(event.inputBuffer.length, remaining)
        const mono = new Float32Array(sampleCount)
        for (let channel = 0; channel < channelCount; channel += 1) {
          const values = event.inputBuffer.getChannelData(channel)
          for (let index = 0; index < sampleCount; index += 1) {
            mono[index] += values[index] / channelCount
          }
        }
        resources.chunks.push(mono)
        resources.sampleCount += sampleCount
        if (resources.sampleCount >= resources.maxSamples) stopAtLimit(resources)
      }
      resources.source.connect(resources.processor)
      resources.processor.connect(resources.silentGain)
      resources.silentGain.connect(resources.context.destination)
      resources.startedAt = now()
      recording.value = true
      resources.durationTimer = setTimer(() => {
        if (active !== resources || !recording.value) return
        durationMs.value = Math.min(maxDurationMs, now() - resources.startedAt)
        if (durationMs.value >= maxDurationMs) stopAtLimit(resources)
      }, 200)
      return true
    } catch (error) {
      await cleanup(resources)
      if (!isCurrent(resources, ownGeneration)) return false
      active = null
      throw error
    } finally {
      if (generation === ownGeneration) starting.value = false
    }
  }

  async function cancelRecording() {
    generation += 1
    const resources = active
    active = null
    starting.value = false
    recording.value = false
    durationMs.value = 0
    limitReached.value = false
    autoStoppedFile.value = null
    await cleanup(resources)
  }

  async function dispose() {
    disposed = true
    await cancelRecording()
  }

  function clearAutoStoppedFile() {
    autoStoppedFile.value = null
  }

  return {
    starting,
    recording,
    finalizing,
    durationMs,
    limitReached,
    autoStoppedFile,
    startRecording,
    stopRecording,
    cancelRecording,
    clearAutoStoppedFile,
    dispose
  }
}

export function useAudioRecorder(options) {
  const recorder = createAudioRecorder(options)
  const preventUnload = (event) => {
    if (!recorder.starting.value && !recorder.recording.value && !recorder.finalizing.value) return
    event.preventDefault()
    event.returnValue = ''
  }
  window.addEventListener('beforeunload', preventUnload)
  window.addEventListener(PWA_BEFORE_UPDATE_EVENT, preventUnload)
  onBeforeUnmount(() => {
    window.removeEventListener('beforeunload', preventUnload)
    window.removeEventListener(PWA_BEFORE_UPDATE_EVENT, preventUnload)
    void recorder.dispose()
  })
  return recorder
}
