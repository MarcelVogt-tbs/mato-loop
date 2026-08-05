import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { DEFAULT_WEBFETCH_USER_AGENT } from 'src/utils/webfetch'

const defaultWebFetch = () => ({
  enabled: true,
  userAgent: DEFAULT_WEBFETCH_USER_AGENT,
  maxResponseMiB: 64,
  defaultPolicy: 'auto_public',
  allowedPrivateOrigins: []
})
const defaultWebSearch = () => ({
  primaryProvider: 'no_key',
  fallbackProvider: null,
  providerOptions: [
    {
      value: 'no_key',
      name: 'Websuche ohne API-Key',
      adapterType: 'ddgs',
      integrationId: null
    }
  ]
})

const defaultMediaPolicy = () => ({ policy: 'disabled', timeoutSeconds: 300 })
const defaultImageEditPolicy = () => ({ policy: 'ask', timeoutSeconds: 300 })
const defaultAgentIdentityPolicy = () => ({ policy: 'ask' })

function settingsPayload(state) {
  return {
    groups: state.groups,
    policies: state.policies,
    webSearch: state.webSearch,
    webFetch: state.webFetch,
    generateImage: state.generateImage,
    editImage: state.editImage,
    generateSpeech: state.generateSpeech,
    generateAudio: state.generateAudio,
    transcribeAudio: state.transcribeAudio,
    updateAgentIdentity: state.updateAgentIdentity
  }
}

export const useToolSettingsStore = defineStore('tool-settings', {
  state: () => ({
    groups: [],
    policies: {},
    webSearch: defaultWebSearch(),
    webFetch: defaultWebFetch(),
    generateImage: defaultMediaPolicy(),
    editImage: defaultImageEditPolicy(),
    generateSpeech: defaultMediaPolicy(),
    generateAudio: defaultMediaPolicy(),
    transcribeAudio: defaultMediaPolicy(),
    updateAgentIdentity: defaultAgentIdentityPolicy(),
    loaded: false,
    loadFailed: false,
    loading: false,
    saving: false
  }),
  actions: {
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/tools/settings')
        this.assignSettings(data)
        this.loaded = true
        return settingsPayload(this)
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async saveSettings(settings) {
      this.saving = true
      try {
        const policies = { ...this.policies, ...(settings.policies || {}) }
        for (const [field, capability] of Object.entries({
          generateImage: 'media.generate.image',
          editImage: 'media.edit.image',
          generateSpeech: 'media.generate.speech',
          generateAudio: 'media.generate.audio',
          transcribeAudio: 'media.transcribe.audio',
          updateAgentIdentity: 'settings.agent_identity.update'
        })) {
          if (settings[field]?.policy) policies[capability] = settings[field].policy
        }
        const payload = {
          policies,
          webSearch: {
            primaryProvider: settings.webSearch
              ? settings.webSearch.primaryProvider
              : this.webSearch.primaryProvider,
            fallbackProvider: settings.webSearch
              ? settings.webSearch.fallbackProvider
              : this.webSearch.fallbackProvider
          },
          webFetch: settings.webFetch || this.webFetch,
          generateImage: { ...this.generateImage, ...settings.generateImage },
          editImage: { ...this.editImage, ...settings.editImage },
          generateSpeech: { ...this.generateSpeech, ...settings.generateSpeech },
          generateAudio: { ...this.generateAudio, ...settings.generateAudio },
          transcribeAudio: { ...this.transcribeAudio, ...settings.transcribeAudio },
          updateAgentIdentity: {
            ...this.updateAgentIdentity,
            ...settings.updateAgentIdentity
          }
        }
        const { data } = await api.put('/tools/settings', payload)
        this.assignSettings(data)
        this.loaded = true
        return settingsPayload(this)
      } finally {
        this.saving = false
      }
    },
    assignSettings(data) {
      this.groups = Array.isArray(data?.groups) ? data.groups : []
      this.policies = { ...(data?.policies || {}) }
      this.webSearch = { ...defaultWebSearch(), ...data?.webSearch }
      this.webFetch = { ...defaultWebFetch(), ...data?.webFetch }
      for (const field of ['generateImage', 'generateSpeech', 'generateAudio', 'transcribeAudio']) {
        this[field] = { ...defaultMediaPolicy(), ...data?.[field] }
      }
      this.editImage = { ...defaultImageEditPolicy(), ...data?.editImage }
      this.updateAgentIdentity = {
        ...defaultAgentIdentityPolicy(),
        ...data?.updateAgentIdentity
      }
    }
  }
})
