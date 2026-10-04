<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Allgemeine Einstellungen</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">Die öffentliche Adresse dieser MatoLoop-Instanz festlegen.</p>
      </div>
    </header>
    <q-banner v-if="settings.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die allgemeinen Einstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps label="Erneut laden" @click="load" /></template>
    </q-banner>
    <q-form @submit.prevent="save">
      <q-card flat class="app-surface">
        <q-card-section class="q-gutter-md">
          <q-input v-model.trim="publicBaseUrl" outlined type="url" label="Öffentliche MatoLoop-URL (optional)" placeholder="https://matoloop.example.com" maxlength="2083" :disable="settings.loading || settings.saving || !settings.loaded" :rules="[originRule]" lazy-rules hide-bottom-space hint="Basisadresse ohne Pfad. Leer lassen für die Umgebungsvariable oder automatische Erkennung. Änderungen gelten sofort für neue OAuth-Anmeldungen." persistent-hint />
          <div v-if="settings.loaded" class="text-body2">
            <div>Wirksame Adresse: <span class="text-weight-medium">{{ settings.effectivePublicBaseUrl }}</span></div>
            <div class="app-muted">Quelle: {{ sources[settings.publicUrlSource] }}</div>
            <div v-if="settings.effectivePublicBaseUrl.startsWith('http://')" class="app-muted">HTTP überträgt die OAuth-Rückleitung unverschlüsselt. Der OAuth-Anbieter entscheidet, ob er diese Adresse akzeptiert.</div>
          </div>
        </q-card-section>
        <q-card-actions class="q-pa-md">
          <q-btn unelevated no-caps color="primary" icon="save" label="Speichern" type="submit" :loading="settings.saving" :disable="settings.loading || !settings.loaded" />
        </q-card-actions>
      </q-card>
    </q-form>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useGeneralSettingsStore } from 'src/features/settings/general-settings-store'

defineOptions({ name: 'GeneralSettingsPage' })
const settings = useGeneralSettingsStore()
const $q = useQuasar()
const publicBaseUrl = ref('')
const sources = { settings: 'Administration', environment: 'Umgebungsvariable', automatic: 'Automatische Erkennung' }
function originRule(value) {
  if (!value) return true
  try {
    const url = new URL(value)
    if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash && url.pathname === '/') return true
  } catch { /* Display the controlled validation message. */ }
  return 'Gib eine HTTP- oder HTTPS-Basisadresse ohne Zugangsdaten, Pfad, Abfrage oder Fragment ein.'
}
async function load() {
  try { publicBaseUrl.value = (await settings.fetchSettings()).publicBaseUrl || '' } catch { /* Central API toast. */ }
}
async function save() {
  try {
    publicBaseUrl.value = (await settings.saveSettings(publicBaseUrl.value)).publicBaseUrl || ''
    $q.notify({ type: 'positive', message: 'Allgemeine Einstellungen gespeichert.' })
  } catch { /* Central API toast. */ }
}
onMounted(load)
</script>
