import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const FILE_MEDIA_DEFAULTS = {
  maxUploadMib: 64,
  maxAttachmentsPerMessage: 8,
  maxMessageUploadMib: 512,
  sessionQuotaMib: 500,
  maxImagePreviewMib: 5,
  maxEditableTextMib: 1,
  maxAudioDurationSeconds: 3600,
  maxVideoDurationSeconds: 900,
  maxExtractedCharacters: 1_000_000,
  maxPdfPages: 1000,
  maxWorkbookSheets: 100,
  maxWorkbookCells: 1_000_000,
  maxPresentationSlides: 500,
  maxArchiveEntries: 5000,
  maxArchiveUncompressedMib: 256,
  maxArchiveEntryMib: 64,
  maxArchiveCompressionRatio: 200
}

export const useFileMediaSettingsStore = defineStore('file-media-settings', {
  state: () => ({
    ...FILE_MEDIA_DEFAULTS,
    loaded: false,
    loading: false,
    loadFailed: false,
    saving: false
  }),
  getters: {
    maxUploadBytes: (state) => state.maxUploadMib * 1024 * 1024,
    maxMessageUploadBytes: (state) => state.maxMessageUploadMib * 1024 * 1024,
    maxImagePreviewBytes: (state) => state.maxImagePreviewMib * 1024 * 1024
  },
  actions: {
    assignSettings(data) {
      for (const key of Object.keys(FILE_MEDIA_DEFAULTS)) {
        if (Number.isFinite(Number(data?.[key]))) this[key] = Number(data[key])
      }
    },
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/file-media-settings')
        this.assignSettings(data)
        this.loaded = true
        return data
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
        const { data } = await api.put('/file-media-settings', settings)
        this.assignSettings(data)
        this.loaded = true
        return data
      } finally {
        this.saving = false
      }
    }
  }
})
