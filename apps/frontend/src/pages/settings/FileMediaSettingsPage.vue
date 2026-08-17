<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Dateien und Medien</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Globale Upload-, Speicher-, Vorschau- und Verarbeitungsgrenzen verwalten.
        </p>
      </div>
      <q-btn
        unelevated
        no-caps
        color="primary"
        icon="save"
        label="Speichern"
        :loading="settings.saving"
        :disable="settings.loading || !settings.loaded"
        @click="save"
      />
    </header>

    <q-banner v-if="settings.loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Datei- und Medieneinstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <q-form ref="formRef" @submit.prevent="save">
      <q-card flat class="app-surface q-mb-md">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Uploads und Speicher</div>
          <div class="text-caption app-muted">Die Session-Quote gilt für den vollständigen Dateibaum jeder Session.</div>
        </q-card-section>
        <q-separator />
        <q-card-section class="row q-col-gutter-md">
          <q-input v-model.number="form.maxUploadMib" class="col-12 col-sm-6" outlined type="number" label="Maximale Uploadgröße (MiB)" :rules="[range(1, 256)]" />
          <q-input v-model.number="form.maxAttachmentsPerMessage" class="col-12 col-sm-6" outlined type="number" label="Anhänge pro Nachricht" :rules="[range(1, 16)]" />
          <q-input v-model.number="form.maxMessageUploadMib" class="col-12 col-sm-6" outlined type="number" label="Gesamtgröße pro Nachricht (MiB)" :rules="[range(1, 512)]" />
          <q-input v-model.number="form.sessionQuotaMib" class="col-12 col-sm-6" outlined type="number" label="Session-Speicherquote (MiB)" :rules="[range(1, 102400)]" />
        </q-card-section>
      </q-card>

      <q-card flat class="app-surface q-mb-md">
        <q-card-section><div class="text-subtitle1 text-weight-bold">Vorschau und Bearbeitung</div></q-card-section>
        <q-separator />
        <q-card-section class="row q-col-gutter-md">
          <q-input v-model.number="form.maxImagePreviewMib" class="col-12 col-sm-6" outlined type="number" label="Bildvorschau (MiB)" :rules="[range(1, 64)]" />
          <q-input v-model.number="form.maxEditableTextMib" class="col-12 col-sm-6" outlined type="number" label="Editierbare Textdatei (MiB)" :rules="[range(1, 16)]" />
        </q-card-section>
      </q-card>

      <q-card flat class="app-surface q-mb-md">
        <q-card-section><div class="text-subtitle1 text-weight-bold">Medien und Dokumente</div></q-card-section>
        <q-separator />
        <q-card-section class="row q-col-gutter-md">
          <q-input v-model.number="form.maxAudioDurationSeconds" class="col-12 col-sm-6" outlined type="number" label="Maximale Audiodauer (Sekunden)" :rules="[range(1, 3600)]" />
          <q-input v-model.number="form.maxVideoDurationSeconds" class="col-12 col-sm-6" outlined type="number" label="Maximale Videodauer (Sekunden)" :rules="[range(1, 900)]" />
          <q-input v-model.number="form.maxExtractedCharacters" class="col-12 col-sm-6" outlined type="number" label="Extrahierte Zeichen" :rules="[range(1000, 10000000)]" />
          <q-input v-model.number="form.maxPdfPages" class="col-12 col-sm-6" outlined type="number" label="PDF-Seiten" :rules="[range(1, 5000)]" />
          <q-input v-model.number="form.maxWorkbookSheets" class="col-12 col-sm-6" outlined type="number" label="Tabellenblätter" :rules="[range(1, 500)]" />
          <q-input v-model.number="form.maxWorkbookCells" class="col-12 col-sm-6" outlined type="number" label="Tabellenzellen" :rules="[range(1000, 5000000)]" />
          <q-input v-model.number="form.maxPresentationSlides" class="col-12 col-sm-6" outlined type="number" label="Präsentationsfolien" :rules="[range(1, 2000)]" />
        </q-card-section>
      </q-card>

      <q-card flat class="app-surface">
        <q-card-section><div class="text-subtitle1 text-weight-bold">Archive</div></q-card-section>
        <q-separator />
        <q-card-section class="row q-col-gutter-md">
          <q-input v-model.number="form.maxArchiveEntries" class="col-12 col-sm-6" outlined type="number" label="Archiveinträge" :rules="[range(1, 20000)]" />
          <q-input v-model.number="form.maxArchiveUncompressedMib" class="col-12 col-sm-6" outlined type="number" label="Entpackte Gesamtgröße (MiB)" :rules="[range(1, 1024)]" />
          <q-input v-model.number="form.maxArchiveEntryMib" class="col-12 col-sm-6" outlined type="number" label="Einzeldatei im Archiv (MiB)" :rules="[range(1, 256)]" />
          <q-input v-model.number="form.maxArchiveCompressionRatio" class="col-12 col-sm-6" outlined type="number" label="Kompressionsverhältnis" :rules="[range(1, 500)]" />
        </q-card-section>
      </q-card>
    </q-form>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { FILE_MEDIA_DEFAULTS, useFileMediaSettingsStore } from 'stores/file-media-settings'

defineOptions({ name: 'FileMediaSettingsPage' })

const $q = useQuasar()
const settings = useFileMediaSettingsStore()
const formRef = ref(null)
const form = reactive({ ...FILE_MEDIA_DEFAULTS })
const range = (minimum, maximum) => (value) => (
  Number.isInteger(Number(value)) && Number(value) >= minimum && Number(value) <= maximum
) || `Wert zwischen ${minimum.toLocaleString('de')} und ${maximum.toLocaleString('de')} erforderlich.`

function assignForm(data) {
  for (const key of Object.keys(FILE_MEDIA_DEFAULTS)) form[key] = Number(data[key])
}

async function load() {
  try {
    assignForm(await settings.fetchSettings())
  } catch {
    // The banner and central interceptor display the error.
  }
}

async function save() {
  if (!await formRef.value?.validate()) return
  try {
    assignForm(await settings.saveSettings({ ...form }))
    $q.notify({ type: 'positive', message: 'Datei- und Medienlimits gespeichert.' })
  } catch {
    // The central API interceptor displays the error.
  }
}

onMounted(load)
</script>
