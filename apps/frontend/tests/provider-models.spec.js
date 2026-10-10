import { describe, expect, it } from 'vitest'

import {
  discoveryState,
  isModelEffective,
  modelOption,
  modelSupportsCapability,
  normalizeModelCapabilities
} from '../src/utils/provider-models'

const provider = { id: 'provider-1', name: 'Lokal', enabled: true, adapterType: 'openai-compatible' }
const model = {
  id: 'model-1',
  providerId: provider.id,
  modelId: 'qwen/test',
  name: 'Qwen Test',
  enabled: true,
  availability: 'available'
}

describe('effective model state', () => {
  it('only exposes models whose provider and discovery state are available', () => {
    expect(isModelEffective(model, provider)).toBe(true)
    expect(isModelEffective({ ...model, availability: 'missing' }, provider)).toBe(false)
    expect(isModelEffective(model, { ...provider, enabled: false })).toBe(false)
  })

  it('requires a saved Azure Speech credential for an effective transcription model', () => {
    const azure = { ...provider, adapterType: 'azure-speech', hasCredential: true }
    const transcription = { ...model, capabilities: ['audio_transcription'] }
    expect(isModelEffective(transcription, azure)).toBe(true)
    expect(isModelEffective(transcription, { ...azure, hasCredential: false })).toBe(false)
    expect(isModelEffective(transcription, { ...azure, effectiveEnabled: false })).toBe(false)
  })

  it('uses effective trust instead of the nullable model override', () => {
    const inheritedPrivateModel = {
      ...model,
      trustLevel: null,
      effectiveTrustLevel: 'private'
    }

    expect(modelOption(inheritedPrivateModel, provider)).toMatchObject({
      trustLevel: 'private',
      trustIcon: 'lock'
    })
  })

  it('treats legacy models as chat models and keeps media capabilities separate from image input', () => {
    expect(normalizeModelCapabilities()).toEqual(['chat'])
    expect(normalizeModelCapabilities(null)).toEqual(['chat'])
    expect(normalizeModelCapabilities([])).toEqual([])
    expect(modelSupportsCapability(model, 'chat')).toBe(true)
    expect(modelSupportsCapability({ ...model, capabilities: [] }, 'chat')).toBe(false)

    const imageInputModel = { ...model, supportsImageInput: true }
    expect(modelSupportsCapability(imageInputModel, 'image_generation')).toBe(false)

    const imageModel = { ...model, capabilities: ['image_generation'], supportsImageInput: false }
    expect(modelSupportsCapability(imageModel, 'chat')).toBe(false)
    expect(modelSupportsCapability(imageModel, 'image_generation')).toBe(true)
  })

  it('distinguishes idle, running, successful and failed discovery', () => {
    expect(discoveryState(provider)).toBe('idle')
    expect(discoveryState(provider, true)).toBe('syncing')
    expect(discoveryState(provider, false, true)).toBe('failed')
    expect(discoveryState({ ...provider, modelsLastSyncedAt: '2026-07-19T12:00:00Z' })).toBe('synced')
  })
})
