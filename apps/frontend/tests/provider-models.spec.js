import { describe, expect, it } from 'vitest'

import {
  discoveryState,
  isModelEffective,
  modelOption,
  modelSupportsCapability,
  normalizeModelCapabilities,
  selectedModelState
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

  it('keeps unavailable session models readable without exposing a UUID', () => {
    expect(selectedModelState(model.id, [{ ...model, enabled: false }], [provider])).toEqual({
      available: false,
      label: 'Qwen Test · Lokal',
      message: 'Das Modell dieser Session ist deaktiviert.'
    })
    expect(selectedModelState('removed-id', [], [])).toEqual({
      available: false,
      label: 'Nicht mehr vorhandenes Modell',
      message: 'Das Modell dieser Session ist nicht mehr vorhanden.'
    })
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

  it('marks a media-only session model unavailable for chat', () => {
    expect(selectedModelState(
      model.id,
      [{ ...model, capabilities: ['audio_transcription'] }],
      [provider]
    )).toEqual({
      available: false,
      label: 'Qwen Test · Lokal',
      message: 'Das Modell dieser Session unterstützt keinen Chat.'
    })
  })

  it('distinguishes idle, running, successful and failed discovery', () => {
    expect(discoveryState(provider)).toBe('idle')
    expect(discoveryState(provider, true)).toBe('syncing')
    expect(discoveryState(provider, false, true)).toBe('failed')
    expect(discoveryState({ ...provider, modelsLastSyncedAt: '2026-07-19T12:00:00Z' })).toBe('synced')
  })
})
