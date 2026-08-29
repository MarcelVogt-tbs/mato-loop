import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const defaultMediaPolicy = () => ({ policy: 'disabled', timeoutSeconds: 300 })
const defaultImageEditPolicy = () => ({ policy: 'ask', timeoutSeconds: 300 })
const defaultVideoAnalysisPolicy = () => ({ policy: 'ask', timeoutSeconds: 1800 })
const defaultAgentIdentityPolicy = () => ({ policy: 'ask' })
const defaultShellPrefixes = () => ({ read: [], write: [] })
const defaultConfigurationWebFetch = () => ({ userAgent: '', maxResponseMiB: 64 })

function settingsPayload(state) {
  return {
    groups: state.groups,
    policies: state.policies,
    shellPrefixes: state.shellPrefixes,
    generateImage: state.generateImage,
    editImage: state.editImage,
    generateSpeech: state.generateSpeech,
    generateAudio: state.generateAudio,
    transcribeAudio: state.transcribeAudio,
    analyzeVideo: state.analyzeVideo,
    updateAgentIdentity: state.updateAgentIdentity
  }
}

function branchConfig(scope, trust, config = {}) {
  return { ...config, params: { ...(config.params || {}), scope, trust } }
}

function contextIsCurrent(store, scope, trust, version) {
  return store.scope === scope && store.trust === trust && store.loadVersion === version
}

export const useToolSettingsStore = defineStore('tool-settings', {
  state: () => ({
    groups: [],
    policies: {},
    shellPrefixes: defaultShellPrefixes(),
    generateImage: defaultMediaPolicy(),
    editImage: defaultImageEditPolicy(),
    generateSpeech: defaultMediaPolicy(),
    generateAudio: defaultMediaPolicy(),
    transcribeAudio: defaultMediaPolicy(),
    analyzeVideo: defaultVideoAnalysisPolicy(),
    updateAgentIdentity: defaultAgentIdentityPolicy(),
    transcribeAudioAvailable: true,
    toolAvailability: {},
    resourceAvailability: {},
    scope: 'instance',
    trust: 'private',
    personalSettingsEnabled: true,
    loaded: false,
    loadFailed: false,
    loading: false,
    saving: false,
    loadVersion: 0,
    saveVersion: 0,
    configurationGroups: [],
    configurationWebFetch: defaultConfigurationWebFetch(),
    configurationLoaded: false,
    configurationLoadFailed: false,
    configurationLoading: false,
    configurationSaving: false,
    configurationLoadVersion: 0,
    configurationSaveVersion: 0
  }),
  actions: {
    selectScope(scope) {
      const next = scope === 'personal' ? 'personal' : 'instance'
      if (next === this.scope) return
      this.scope = next
      this.invalidatePolicyRequests()
    },
    selectTrust(trust) {
      const next = trust === 'public' ? 'public' : 'private'
      if (next === this.trust) return
      this.trust = next
      this.invalidatePolicyRequests()
    },
    invalidatePolicyRequests() {
      this.loadVersion += 1
      this.saveVersion += 1
      this.loading = false
      this.saving = false
      this.loaded = false
    },
    async fetchSettings(scope = this.scope, trust = this.trust) {
      const loadVersion = ++this.loadVersion
      this.loading = true
      this.loadFailed = false
      try {
        let gate = null
        if (scope === 'personal') {
          gate = (await api.get('/tools/personal-settings')).data
          if (!contextIsCurrent(this, scope, trust, loadVersion)) return
          this.personalSettingsEnabled = gate.enabled !== false
          if (!this.personalSettingsEnabled) {
            this.assignSettings({})
            this.loaded = true
            return settingsPayload(this)
          }
        }
        const [{ data }, availability] = await Promise.all([
          api.get('/tools/settings', branchConfig(scope, trust)),
          api.get('/tools/availability')
        ])
        if (!contextIsCurrent(this, scope, trust, loadVersion)) return
        this.assignAvailability(availability.data)
        this.assignSettings(data)
        if (gate) this.personalSettingsEnabled = gate.enabled !== false
        this.loaded = true
        return settingsPayload(this)
      } catch (error) {
        if (contextIsCurrent(this, scope, trust, loadVersion)) this.loadFailed = true
        throw error
      } finally {
        if (contextIsCurrent(this, scope, trust, loadVersion)) this.loading = false
      }
    },
    async fetchAvailability() {
      const loadVersion = ++this.loadVersion
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/tools/availability')
        if (this.loadVersion !== loadVersion) return
        this.assignAvailability(data)
        this.loaded = true
      } catch (error) {
        if (this.loadVersion === loadVersion) this.loadFailed = true
        throw error
      } finally {
        if (this.loadVersion === loadVersion) this.loading = false
      }
    },
    async saveSettings(settings) {
      const scope = this.scope
      const trust = this.trust
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
          generateImage: { ...this.generateImage, ...settings.generateImage },
          editImage: { ...this.editImage, ...settings.editImage },
          generateSpeech: { ...this.generateSpeech, ...settings.generateSpeech },
          generateAudio: { ...this.generateAudio, ...settings.generateAudio },
          transcribeAudio: { ...this.transcribeAudio, ...settings.transcribeAudio },
          analyzeVideo: { ...this.analyzeVideo, ...settings.analyzeVideo },
          updateAgentIdentity: { ...this.updateAgentIdentity, ...settings.updateAgentIdentity }
        }
        const { data } = await api.put('/tools/settings', payload, branchConfig(scope, trust))
        if (this.scope !== scope || this.trust !== trust || this.saveVersion !== saveVersion) return
        this.assignSettings(data)
        this.loaded = true
        return settingsPayload(this)
      } finally {
        if (this.saveVersion === saveVersion) this.saving = false
      }
    },
    async resetPersonalSettings() {
      const scope = this.scope
      const trust = this.trust
      const saveVersion = ++this.saveVersion
      this.loadVersion += 1
      this.saving = true
      try {
        await api.delete('/tools/settings', { params: { scope: 'personal' } })
        if (this.scope !== scope || this.trust !== trust || this.saveVersion !== saveVersion) return
        const { data } = await api.get('/tools/settings', branchConfig(scope, trust))
        if (this.scope !== scope || this.trust !== trust || this.saveVersion !== saveVersion) return
        this.assignSettings(data)
        this.loaded = true
        return settingsPayload(this)
      } finally {
        if (this.saveVersion === saveVersion) this.saving = false
      }
    },
    async setPersonalSettingsEnabled(enabled) {
      await api.patch('/tools/personal-enabled', { enabled }, { params: { scope: 'instance' } })
      this.personalSettingsEnabled = enabled
    },
    async fetchConfiguration() {
      const loadVersion = ++this.configurationLoadVersion
      this.configurationLoading = true
      this.configurationLoadFailed = false
      try {
        const { data } = await api.get('/tools/configuration')
        if (this.configurationLoadVersion !== loadVersion) return
        this.assignConfiguration(data)
        this.configurationLoaded = true
        return data
      } catch (error) {
        if (this.configurationLoadVersion === loadVersion) this.configurationLoadFailed = true
        throw error
      } finally {
        if (this.configurationLoadVersion === loadVersion) this.configurationLoading = false
      }
    },
    async saveConfiguration(configuration) {
      const saveVersion = ++this.configurationSaveVersion
      this.configurationLoadVersion += 1
      this.configurationSaving = true
      try {
        const { data } = await api.put('/tools/configuration', configuration)
        if (this.configurationSaveVersion !== saveVersion) return
        this.assignConfiguration(data)
        this.configurationLoaded = true
        return data
      } finally {
        if (this.configurationSaveVersion === saveVersion) this.configurationSaving = false
      }
    },
    assignConfiguration(data) {
      this.configurationGroups = Array.isArray(data?.groups)
        ? data.groups.map((group) => ({
            ...group,
            tools: (group.tools || []).map((tool) => ({
              ...tool,
              resources: (tool.resources || []).map((resource) => ({ ...resource }))
            }))
          }))
        : []
      this.configurationWebFetch = {
        ...defaultConfigurationWebFetch(),
        ...data?.webFetch
      }
      this.assignAvailability({
        tools: data?.toolAvailability,
        resources: data?.resourceAvailability
      })
    },
    assignAvailability(data) {
      this.toolAvailability = { ...data?.tools }
      this.resourceAvailability = Object.fromEntries(
        Object.entries(data?.resources || {}).map(([tool, resources]) => [tool, { ...resources }])
      )
      this.groups = this.groups.map((group) => ({
        ...group,
        tools: group.tools.map((tool) => ({
          ...tool,
          available: this.toolAvailability[tool.name] !== false,
          actions: (tool.actions || []).map((action) => ({
            ...action,
            available: this.resourceAvailability[tool.name]?.[action.capability] !== false
          }))
        }))
      }))
      this.transcribeAudioAvailable = this.toolAvailability.transcribe_audio !== false
    },
    assignSettings(data) {
      if ('personalSettingsEnabled' in (data || {})) {
        this.personalSettingsEnabled = data.personalSettingsEnabled !== false
      }
      this.groups = (Array.isArray(data?.groups) ? data.groups : [])
        .map((group) => ({
          ...group,
          tools: (group.tools || [])
            .filter((tool) => tool.source?.type !== 'integration')
            .map((tool) => ({
              ...tool,
              available: tool.available !== false && this.toolAvailability[tool.name] !== false,
              actions: (tool.actions || []).map((action) => ({
                ...action,
                available: action.available !== false
                  && this.resourceAvailability[tool.name]?.[action.capability] !== false
              }))
            }))
        }))
        .filter((group) => group.tools.length)
      const builtinCapabilities = new Set(this.groups.flatMap((group) => (
        group.tools.flatMap((tool) => (
          (tool.actions || []).filter((action) => action.configurable).map((action) => action.capability)
        ))
      )))
      this.policies = Object.fromEntries(
        Object.entries(data?.policies || {}).filter(([capability]) => builtinCapabilities.has(capability))
      )
      this.shellPrefixes = {
        read: Array.isArray(data?.shellPrefixes?.read) ? data.shellPrefixes.read.map((item) => [...item]) : [],
        write: Array.isArray(data?.shellPrefixes?.write) ? data.shellPrefixes.write.map((item) => [...item]) : []
      }
      for (const field of ['generateImage', 'generateSpeech', 'generateAudio', 'transcribeAudio']) {
        this[field] = { ...defaultMediaPolicy(), ...data?.[field] }
      }
      this.editImage = { ...defaultImageEditPolicy(), ...data?.editImage }
      this.analyzeVideo = { ...defaultVideoAnalysisPolicy(), ...data?.analyzeVideo }
      this.updateAgentIdentity = { ...defaultAgentIdentityPolicy(), ...data?.updateAgentIdentity }
    }
  }
})
