import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import { sessionModel } from '../src/features/chat/session-model'
import { useProviderStore } from '../src/stores/providers'

vi.mock('src/boot/axios', () => ({ api: {} }))

it('resolves each agent override above session and effective defaults', () => {
  setActivePinia(createPinia())
  const providers = useProviderStore()
  providers.modelDefaults = { defaultModelId: 'global', reasoningEffort: 'low', profileModels: { build: { modelId: 'build-model', reasoningEffort: 'high' } } }
  providers.providers = [{ id: 'provider', enabled: true }]
  providers.models = ['plan-model', 'session', 'global', 'build-model'].map((id) => ({ id, providerId: 'provider', enabled: true, supportsTools: true, reasoningEfforts: id === 'build-model' ? ['high'] : ['low'] }))
  const session = { modelId: 'session', sessionProfile: { agentModels: { plan: { modelId: 'plan-model', reasoningEffort: 'high' } } } }
  const selections = [sessionModel(session, providers, 'plan'), sessionModel(session, providers, 'build'), sessionModel({}, providers), sessionModel({}, providers, 'build')]
  providers.models[0].availability = 'missing'
  providers.models[1].supportsTools = false
  selections.push(sessionModel(session, providers, 'plan'))
  expect(selections).toEqual([
    { modelId: 'plan-model', reasoningEffort: 'high' }, { modelId: 'session', reasoningEffort: null }, { modelId: 'global', reasoningEffort: 'low' }, { modelId: 'build-model', reasoningEffort: 'high' }, { modelId: 'global', reasoningEffort: null }
  ])
})
