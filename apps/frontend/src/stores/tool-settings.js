import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const defaultWebFetch = () => ({
  enabled: true,
  defaultPolicy: 'ask',
  allowedPrivateOrigins: []
})

const defaultMediaPolicy = () => ({ policy: 'disabled', timeoutSeconds: 300 })

function settingsPayload(state) {
  return {
    webFetch: state.webFetch,
    generateImage: state.generateImage,
    generateSpeech: state.generateSpeech,
    generateAudio: state.generateAudio,
    transcribeAudio: state.transcribeAudio
  }
}

export const useToolSettingsStore = defineStore('tool-settings', {
  state: () => ({
    webFetch: defaultWebFetch(),
    generateImage: defaultMediaPolicy(),
    generateSpeech: defaultMediaPolicy(),
    generateAudio: defaultMediaPolicy(),
    transcribeAudio: defaultMediaPolicy(),
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
        const payload = {
          webFetch: settings.webFetch || this.webFetch,
          generateImage: { ...this.generateImage, ...settings.generateImage },
          generateSpeech: { ...this.generateSpeech, ...settings.generateSpeech },
          generateAudio: { ...this.generateAudio, ...settings.generateAudio },
          transcribeAudio: { ...this.transcribeAudio, ...settings.transcribeAudio }
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
      this.webFetch = { ...defaultWebFetch(), ...data?.webFetch }
      for (const field of ['generateImage', 'generateSpeech', 'generateAudio', 'transcribeAudio']) {
        this[field] = { ...defaultMediaPolicy(), ...data?.[field] }
      }
    }
  }
})
