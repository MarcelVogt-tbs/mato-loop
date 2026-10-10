<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Darstellung</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">Name, Logo und Themes dieser Instanz verwalten.</p>
      </div>
    </header>
    <q-tabs v-model="tab" dense no-caps align="left" active-color="primary" indicator-color="primary" class="q-mb-lg">
      <q-tab name="branding" icon="edit" label="Anpassung" />
      <q-tab name="themes" icon="palette" label="Themes" />
    </q-tabs>
    <q-banner v-if="appearance.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Darstellungseinstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps label="Erneut laden" @click="load" /></template>
    </q-banner>
    <q-banner v-if="appearance.loaded && (appearance.defaultThemeId !== appearance.effectiveThemeId || appearance.appliedThemeId !== appearance.effectiveThemeId)" rounded class="app-surface-tonal q-mb-md">
      Das gespeicherte Theme kann momentan nicht verwendet werden. Angezeigt wird: {{ appliedThemeName }}.
      <template #action><q-btn flat no-caps label="Erneut laden" @click="load" /></template>
    </q-banner>
    <q-tab-panels v-model="tab" class="bg-transparent">
      <q-tab-panel name="branding" class="q-pa-none">
        <q-form @submit.prevent="saveBranding">
          <q-card flat class="app-surface">
            <q-card-section class="q-gutter-md">
              <q-input v-model.trim="appName" dense outlined label="App-Name" maxlength="80" :disable="busy" :rules="[value => Boolean(value?.trim()) || 'Bitte gib einen Namen ein.']" hide-bottom-space />
              <q-input v-model.trim="description" dense outlined type="textarea" autogrow label="Kurzbeschreibung (optional)" maxlength="500" :disable="busy" />
              <div class="row items-center q-gutter-md">
                <img :src="appearance.logoUrl" width="64" height="64" alt="Aktuelles Logo" />
                <div class="col">
                  <q-file :model-value="null" dense outlined accept="image/png,image/jpeg,image/webp" :max-file-size="1048576" label="Logo hochladen" :disable="busy" @update:model-value="uploadLogo" @rejected="rejectLogo" />
                </div>
              </div>
              <p class="text-caption app-muted q-mb-none">PNG, JPEG oder WebP, maximal 1 MiB. Das Bild wird proportional in ein quadratisches 512-px-Logo eingepasst und auch als App-Icon verwendet.</p>
              <q-btn v-if="appearance.customLogo" flat no-caps icon="restore" label="Standardlogo wiederherstellen" :disable="busy" @click="resetLogo" />
            </q-card-section>
            <q-card-actions class="q-pa-md">
              <q-btn unelevated no-caps color="primary" icon="save" label="Speichern" type="submit" :loading="appearance.saving" :disable="busy" />
            </q-card-actions>
          </q-card>
        </q-form>
      </q-tab-panel>
      <q-tab-panel name="themes" class="q-pa-none">
        <q-form @submit.prevent="saveThemes">
          <q-card flat class="app-surface">
            <q-card-section>
              <AppSelect v-model="defaultThemeId" dense outlined emit-value map-options label="Standard-Theme" :options="enabledThemes" :disable="busy" />
              <p class="text-caption app-muted q-mt-sm q-mb-none">Gilt für alle Benutzer und die Anmeldung. Das gewählte Standard-Theme bleibt aktiviert.</p>
              <p class="text-caption app-muted q-mb-none">Aktuell angezeigt: {{ appliedThemeName }}</p>
            </q-card-section>
            <q-list separator>
              <q-item v-for="theme in appearance.themes" :key="theme.id" class="settings-list-item q-py-md">
                <q-item-section class="col-12 col-sm">
                  <q-item-label class="settings-list-name">{{ theme.name }}</q-item-label>
                  <q-item-label caption>{{ theme.id === defaultThemeId ? 'Standard-Theme' : disabledThemeIds.includes(theme.id) ? 'Deaktiviert' : 'Aktiviert' }}</q-item-label>
                </q-item-section>
                <q-item-section side top class="settings-list-actions">
                  <q-toggle dense color="primary" :model-value="!disabledThemeIds.includes(theme.id)" :disable="busy || theme.id === defaultThemeId" :aria-label="`${theme.name} ${disabledThemeIds.includes(theme.id) ? 'aktivieren' : 'deaktivieren'}`" @update:model-value="setEnabled(theme.id, $event)" />
                </q-item-section>
              </q-item>
            </q-list>
            <q-card-actions class="q-pa-md">
              <q-btn unelevated no-caps color="primary" icon="save" label="Speichern" type="submit" :loading="appearance.saving" :disable="busy" />
            </q-card-actions>
          </q-card>
        </q-form>
      </q-tab-panel>
    </q-tab-panels>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { useConfirm } from 'src/composables/confirm'
import { notifyError } from 'src/utils/notifications'
import { useAppearanceStore } from './appearance-store'

defineOptions({ name: 'AppearanceSettingsPage' })
const appearance = useAppearanceStore()
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const tab = computed({
  get: () => route.query.tab === 'themes' ? 'themes' : 'branding',
  set: value => router.replace({ query: { ...route.query, tab: value } })
})
const appName = ref(appearance.appName)
const description = ref(appearance.description)
const defaultThemeId = ref(appearance.defaultThemeId)
const disabledThemeIds = ref([])
const busy = computed(() => !appearance.loaded || appearance.loading || appearance.saving)
const enabledThemes = computed(() => appearance.themes.filter(theme => !disabledThemeIds.value.includes(theme.id)).map(theme => ({ label: theme.name, value: theme.id })))
const appliedThemeName = computed(() => appearance.themes.find(theme => theme.id === appearance.appliedThemeId)?.name || appearance.appliedThemeId)

async function load() {
  try {
    await appearance.fetchAppearance()
    appName.value = appearance.appName
    description.value = appearance.description
    defaultThemeId.value = appearance.defaultThemeId
    disabledThemeIds.value = appearance.themes.filter(theme => !theme.enabled).map(theme => theme.id)
  } catch { /* Central API toast. */ }
}
function setEnabled(id, enabled) {
  disabledThemeIds.value = enabled ? disabledThemeIds.value.filter(value => value !== id) : [...disabledThemeIds.value, id]
}
async function saveBranding() {
  try {
    await appearance.saveBranding({ appName: appName.value, description: description.value })
    $q.notify({ type: 'positive', message: 'Anpassungen gespeichert.' })
  } catch { /* Central API toast. */ }
}
async function saveThemes() {
  try {
    await appearance.saveThemes({ defaultThemeId: defaultThemeId.value, disabledThemeIds: disabledThemeIds.value })
    $q.notify({ type: 'positive', message: 'Theme-Einstellungen gespeichert.' })
  } catch { /* Central API toast. */ }
}
async function uploadLogo(file) {
  if (!file) return
  try {
    await appearance.uploadLogo(file)
    $q.notify({ type: 'positive', message: 'Logo gespeichert.' })
  } catch { /* Central API toast. */ }
}
function rejectLogo(entries) {
  const code = entries.some(entry => entry.failedProp === 'max-file-size') ? 'attachment.tooLarge' : 'attachment.unsupportedType'
  notifyError({ error: { code } })
}
async function resetLogo() {
  if (!await confirm('Soll das eigene Logo durch das Standardlogo ersetzt werden?', 'Logo zurücksetzen?', { okLabel: 'Zurücksetzen' })) return
  try {
    await appearance.resetLogo()
    $q.notify({ type: 'positive', message: 'Standardlogo wiederhergestellt.' })
  } catch { /* Central API toast. */ }
}
onMounted(load)
</script>
