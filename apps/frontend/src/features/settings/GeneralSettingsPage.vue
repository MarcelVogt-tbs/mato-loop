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
      <q-card flat class="app-surface">
        <q-card-section>
          <q-toggle v-model="agentAutoUpdateEnabled" color="primary" label="Agents automatisch aktualisieren" :disable="settings.loading || settings.saving || !settings.loaded" />
          <div class="text-body2 app-muted q-mt-sm">
            Instanzstandard für alle Computer. Einzelne Computer können die Einstellung überschreiben.
            Geprüft wird beim Verbinden und nach Änderungen der Update-Einstellungen. Beschäftigte Agents warten bis zur nächsten Prüfung oder einem manuellen Update.
          </div>
        </q-card-section>
      </q-card>
      <q-card flat class="app-surface q-mt-md">
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
        </q-card-section>
      </q-card>
    </q-form>
    <q-list separator class="app-surface rounded-borders q-mt-md">
      <q-item clickable :to="{ name: 'admin-providers', query: { tab: 'general' } }">
        <q-item-section avatar><q-icon name="security" color="primary" /></q-item-section>
        <q-item-section><q-item-label>Modelltrennung</q-item-label><q-item-label caption>Lege für jeden Provider fest, ob seine Modelle als privat oder öffentlich gelten. Bei aktiver Modelltrennung gelten dafür getrennte Tool-Rechte, Erinnerungen und persönliche Profilangaben.</q-item-label></q-item-section>
        <q-item-section side><q-icon name="open_in_new" /></q-item-section>
      </q-item>
      <q-item clickable :to="{ name: 'admin-tools', query: { tab: 'general' } }">
        <q-item-section avatar><q-icon name="verified_user" color="primary" /></q-item-section>
        <q-item-section><q-item-label>Freigabeverhalten</q-item-label><q-item-label caption>Lege fest, welche Aktionen der Agent standardmäßig automatisch ausführt und wann er um Erlaubnis fragt.</q-item-label></q-item-section>
        <q-item-section side><q-icon name="open_in_new" /></q-item-section>
      </q-item>
    </q-list>
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
const agentAutoUpdateEnabled = ref(false)
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
    agentAutoUpdateEnabled.value = data.agentAutoUpdateEnabled
  } catch { /* Central API toast. */ }
}
async function save() {
  if (settings.loading || settings.saving || !settings.loaded) return
  if (!await formRef.value?.validate()) return
  try {
    const data = await settings.saveSettings({ publicBaseUrl: publicBaseUrl.value, agentAutoUpdateEnabled: agentAutoUpdateEnabled.value })
    publicBaseUrl.value = data.publicBaseUrl || ''
    agentAutoUpdateEnabled.value = data.agentAutoUpdateEnabled
    $q.notify({ type: 'positive', message: 'Grundeinstellungen gespeichert.' })
  } catch { /* Central API toast. */ }
}
onMounted(load)
</script>
