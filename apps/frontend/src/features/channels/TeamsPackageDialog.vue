<template>
  <q-dialog :model-value="true" :maximized="$q.screen.lt.sm" :persistent="downloading" @hide="$emit('close')">
    <q-card class="integration-dialog app-surface column">
      <q-card-section class="text-h6">Teams-App-Paket exportieren</q-card-section>
      <q-separator />
      <q-form class="col column no-wrap" @submit.prevent="download">
        <q-card-section class="col scroll q-gutter-md">
          <div>
            Das Paket übernimmt den globalen Agentennamen und das Instanzlogo aus
            MatoLoop. Es enthält keine Zugangsdaten und wird nicht automatisch in Teams veröffentlicht.
          </div>
          <q-input v-model.trim="form.version" dense outlined label="Paketversion"
            placeholder="1.2.0" maxlength="32" :rules="[versionRule]"
            hint="Für ein Update muss die Version höher sein als im Teams Admin Center." />
          <q-input v-model.trim="form.publisher" dense outlined label="Herausgeber"
            maxlength="32" :rules="[required]" />
          <q-input v-model.trim="form.websiteUrl" dense outlined label="Website (HTTPS)"
            inputmode="url" :rules="[httpsRule]" />
          <q-input v-model.trim="form.privacyUrl" dense outlined label="Datenschutzerklärung (HTTPS)"
            inputmode="url" :rules="[httpsRule]" />
          <q-input v-model.trim="form.termsUrl" dense outlined label="Nutzungsbedingungen (HTTPS)"
            inputmode="url" :rules="[httpsRule]" />
        </q-card-section>
        <q-separator />
        <q-card-actions align="right" class="q-pa-md">
          <q-btn v-close-popup flat no-caps label="Schließen" :disable="downloading" />
          <q-btn unelevated no-caps color="primary" type="submit" label="ZIP herunterladen" :loading="downloading" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { exportFile, useQuasar } from 'quasar'
import { useChannelStore } from 'stores/channels'

defineEmits(['close'])
const $q = useQuasar()
const channels = useChannelStore()
const downloading = ref(false)
const form = reactive({ version: '', publisher: '', websiteUrl: '', privacyUrl: '', termsUrl: '' })
const required = (value) => Boolean(value) || 'Pflichtfeld'
const versionRule = (value) => /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value) || 'Version im Format 1.2.0 eingeben'
const httpsRule = (value) => {
  try {
    const url = new URL(value)
    if (url.protocol === 'https:' && !url.username && !url.password) return true
  } catch {
    // Report invalid URLs on the field.
  }
  return 'Gültige HTTPS-Adresse ohne Zugangsdaten erforderlich'
}

async function download() {
  if (downloading.value) return
  const payload = { ...form }
  downloading.value = true
  try {
    const data = await channels.exportTeamsAppPackage(payload)
    if (exportFile(`matoloop-teams-${payload.version}.zip`, data, 'application/zip') !== true) {
      $q.notify({ type: 'negative', message: 'Der Browser konnte die ZIP-Datei nicht herunterladen.' })
    }
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    downloading.value = false
  }
}
</script>
