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
const defaultVideoAnalysisPolicy = () => ({ policy: 'ask', timeoutSeconds: 1800 })
const defaultAgentIdentityPolicy = () => ({ policy: 'ask' })
const defaultShellPrefixes = () => ({ read: [], write: [] })

function settingsPayload(state) {
  return {
    groups: state.groups,
    policies: state.policies,
    shellPrefixes: state.shellPrefixes,
    webSearch: state.webSearch,
    webFetch: state.webFetch,
    generateImage: state.generateImage,
    editImage: state.editImage,
    generateSpeech: state.generateSpeech,
    generateAudio: state.generateAudio,
    transcribeAudio: state.transcribeAudio,
    analyzeVideo: state.analyzeVideo,
    updateAgentIdentity: state.updateAgentIdentity
  }
}

function scopeConfig(scope, config = {}) {
  return { ...config, params: { ...(config.params || {}), scope } }
}

export const useToolSettingsStore = defineStore('tool-settings', {
  state: () => ({
    groups: [],
    policies: {},
    shellPrefixes: defaultShellPrefixes(),
    webSearch: defaultWebSearch(),
    webFetch: defaultWebFetch(),
    generateImage: defaultMediaPolicy(),
    editImage: defaultImageEditPolicy(),
    generateSpeech: defaultMediaPolicy(),
    generateAudio: defaultMediaPolicy(),
    transcribeAudio: defaultMediaPolicy(),
    analyzeVideo: defaultVideoAnalysisPolicy(),
    updateAgentIdentity: defaultAgentIdentityPolicy(),
    scope: 'instance',
    personalSettingsEnabled: true,
    loaded: false,
    loadFailed: false,
    loading: false,
    saving: false,
    loadVersion: 0,
    saveVersion: 0
  }),
  actions: {
    selectScope(scope) {
      const next = scope === 'personal' ? 'personal' : 'instance'
      if (next === this.scope) return
      this.scope = next
      this.loadVersion += 1
      this.saveVersion += 1
      this.loading = false
      this.saving = false
    },
    async fetchSettings(scope = this.scope) {
      const selectedScope = this.scope
      const loadVersion = ++this.loadVersion
      this.loading = true
      this.loadFailed = false
      try {
        let policy = null
        if (scope === 'personal') {
          policy = (await api.get('/tools/personal-settings')).data
          if (this.scope !== selectedScope || this.loadVersion !== loadVersion) return
          this.personalSettingsEnabled = policy.enabled !== false
          if (!this.personalSettingsEnabled) {
            this.assignSettings({})
            this.loaded = true
            return settingsPayload(this)
          }
        }
        const { data } = await api.get('/tools/settings', scopeConfig(scope))
        if (this.scope !== selectedScope || this.loadVersion !== loadVersion) return
        this.assignSettings(data)
        if (policy) this.personalSettingsEnabled = policy.enabled !== false
        this.loaded = true
        return settingsPayload(this)
      } catch (error) {
        if (this.scope === selectedScope && this.loadVersion === loadVersion) {
          this.loadFailed = true
        }
        throw error
      } finally {
        if (this.scope === selectedScope && this.loadVersion === loadVersion) {
          this.loading = false
        }
      }
    },
    async saveSettings(settings) {
      const scope = this.scope
      const saveVersion = ++this.saveVersion
      this.loadVersion += 1
      this.saving = true
      try {
        const policies = { ...this.policies, ...(settings.policies || {}) }
        for (const [field, capability] of Object.entries({
          generateImage: 'media.generate.image',
          editImage: 'media.edit.image',
          generateSpeech: 'media.generate.speech',
          generateAudio: 'media.generate.audio',
          transcribeAudio: 'media.transcribe.audio',
          analyzeVideo: 'media.analyze.video',
          updateAgentIdentity: 'settings.agent_identity.update'
        })) {
          if (settings[field]?.policy) policies[capability] = settings[field].policy
        }
        const payload = {
          policies,
          shellPrefixes: settings.shellPrefixes || this.shellPrefixes,
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
          analyzeVideo: { ...this.analyzeVideo, ...settings.analyzeVideo },
          updateAgentIdentity: {
            ...this.updateAgentIdentity,
            ...settings.updateAgentIdentity
          }
        }
        const { data } = await api.put('/tools/settings', payload, scopeConfig(scope))
        if (this.scope !== scope || this.saveVersion !== saveVersion) return
        this.assignSettings(data)
        this.loaded = true
        return settingsPayload(this)
      } finally {
        if (this.saveVersion === saveVersion) this.saving = false
      }
    },
    async resetPersonalSettings() {
      const scope = this.scope
      const saveVersion = ++this.saveVersion
      this.loadVersion += 1
      this.saving = true
      try {
        const { data } = await api.delete('/tools/settings', scopeConfig('personal'))
        if (this.scope !== scope || this.saveVersion !== saveVersion) return
        this.assignSettings(data)
        this.loaded = true
        return settingsPayload(this)
      } finally {
        if (this.saveVersion === saveVersion) this.saving = false
      }
    },
    async setPersonalSettingsEnabled(enabled) {
      await api.patch('/tools/personal-enabled', { enabled }, scopeConfig('instance'))
      this.personalSettingsEnabled = enabled
    },
    assignSettings(data) {
      if ('personalSettingsEnabled' in (data || {})) {
        this.personalSettingsEnabled = data.personalSettingsEnabled !== false
      }
      this.groups = Array.isArray(data?.groups) ? data.groups : []
      this.policies = { ...(data?.policies || {}) }
      this.shellPrefixes = {
        read: Array.isArray(data?.shellPrefixes?.read) ? data.shellPrefixes.read.map((item) => [...item]) : [],
        write: Array.isArray(data?.shellPrefixes?.write) ? data.shellPrefixes.write.map((item) => [...item]) : []
      }
      this.webSearch = { ...defaultWebSearch(), ...data?.webSearch }
      this.webFetch = { ...defaultWebFetch(), ...data?.webFetch }
      for (const field of ['generateImage', 'generateSpeech', 'generateAudio', 'transcribeAudio']) {
        this[field] = { ...defaultMediaPolicy(), ...data?.[field] }
      }
      this.editImage = { ...defaultImageEditPolicy(), ...data?.editImage }
      this.analyzeVideo = { ...defaultVideoAnalysisPolicy(), ...data?.analyzeVideo }
      this.updateAgentIdentity = {
        ...defaultAgentIdentityPolicy(),
        ...data?.updateAgentIdentity
      }
    }
  }
})
