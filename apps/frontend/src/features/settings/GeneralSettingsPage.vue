<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Grundeinstellungen der Instanz</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">Instanzweite Grundeinstellungen verwalten.</p>
      </div>
      <q-btn unelevated no-caps color="primary" icon="save" label="Speichern" :loading="settings.saving" :disable="settings.loading || !settings.loaded" @click="save" />
    </header>
    <q-banner v-if="settings.loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Grundeinstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>
    <q-form ref="formRef" @submit.prevent="save">
      <q-card flat class="app-surface q-mb-md">
        <q-card-section>
          <div class="row items-center q-col-gutter-md">
            <div class="col-12 col-sm row items-center no-wrap q-gutter-md">
              <q-avatar color="primary" text-color="white" icon="security" />
              <div class="col">
                <div class="text-subtitle1 text-weight-bold">Modellvertrauen</div>
                <div class="text-caption app-muted">Öffentliche und private Modelle unterscheiden.</div>
              </div>
            </div>
            <q-toggle dense v-model="modelTrustEnabled" class="col-12 col-sm-auto" color="primary" aria-label="Öffentliche und private Modelle unterscheiden" :disable="settings.loading || settings.saving || !settings.loaded" />
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <q-banner rounded class="app-surface-tonal">
            <template #avatar><q-icon name="info_outline" color="primary" /></template>
            Ausgeschaltet werden alle Modelle für neue Läufe als privat behandelt, auch beim Zugriff auf Memories und Profilangaben. Die bisherigen Einstufungen bleiben gespeichert. Pseudonymisierung ist unabhängig davon einstellbar.
          </q-banner>
        </q-card-section>
      </q-card>
      <q-card flat class="app-surface">
        <q-card-section class="row items-center q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="link" />
          <div class="col">
            <div class="text-subtitle1 text-weight-bold">Öffentliche Adresse</div>
            <div class="text-caption app-muted">Öffentlichen Ursprung dieser Instanz für OAuth-Anmeldungen festlegen.</div>
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section class="q-gutter-md">
          <q-input dense v-model.trim="publicBaseUrl" outlined type="url" label="Öffentliche MatoLoop-URL (optional)" placeholder="https://matoloop.example.com" maxlength="2083" :disable="settings.loading || settings.saving || !settings.loaded" :rules="[originRule]" lazy-rules hide-bottom-space hint="Basisadresse ohne Pfad. Leer lassen für die Umgebungsvariable oder automatische Erkennung. Änderungen gelten sofort für neue OAuth-Anmeldungen." persistent-hint />
          <div v-if="settings.loaded" class="text-body2">
            <div>Wirksame Adresse: <span class="text-weight-medium">{{ settings.effectivePublicBaseUrl }}</span></div>
            <div class="app-muted">Quelle: {{ sources[settings.publicUrlSource] }}</div>
            <div v-if="settings.effectivePublicBaseUrl.startsWith('http://')" class="app-muted">HTTP überträgt die OAuth-Rückleitung unverschlüsselt. Der OAuth-Anbieter entscheidet, ob er diese Adresse akzeptiert.</div>
          </div>
        </q-card-section>
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
const formRef = ref(null)
const publicBaseUrl = ref('')
const modelTrustEnabled = ref(true)
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
  try {
    const data = await settings.fetchSettings()
    publicBaseUrl.value = data.publicBaseUrl || ''
    modelTrustEnabled.value = data.modelTrustEnabled
  } catch { /* Central API toast. */ }
}
async function save() {
  if (!await formRef.value?.validate()) return
  try {
    const data = await settings.saveSettings({ publicBaseUrl: publicBaseUrl.value, modelTrustEnabled: modelTrustEnabled.value })
    publicBaseUrl.value = data.publicBaseUrl || ''
    modelTrustEnabled.value = data.modelTrustEnabled
    $q.notify({ type: 'positive', message: 'Grundeinstellungen gespeichert.' })
  } catch { /* Central API toast. */ }
}
onMounted(load)
</script>
