<template>
  <section>
    <header class="settings-section-header">
      <div><h2 class="text-h6 text-weight-bold q-my-none">Standardmodelle</h2><p class="text-body2 app-muted q-mb-none q-mt-xs">{{ admin ? 'Globale Modelle für Agenten und Hilfsfunktionen.' : 'Persönliche Abweichungen von den globalen Standardmodellen.' }}</p></div>
      <q-btn v-if="showManagement" color="primary" unelevated no-caps icon="save" label="Speichern" :loading="store.saving" :disable="blocked || !dirty" @click="save" />
    </header>
    <q-tabs v-if="admin" v-model="section" dense no-caps align="left" active-color="primary" class="q-mb-lg"><q-tab name="settings" label="Einstellungen" icon="settings" /><q-tab name="management" label="Verwaltung" icon="stars" /></q-tabs>
    <PersonalSettingsGate v-if="admin && !showManagement" :model-value="store.personalSettingsEnabled" title="Persönliche Standardmodelle erlauben" description="Benutzer können eigene Modellvorgaben festlegen." :disabled="blocked" @update:model-value="setEnabled" />
    <q-banner v-if="failed" class="app-surface-tonal q-mb-md">Standardmodelle konnten nicht geladen werden.<template #action><q-btn flat label="Erneut laden" @click="load" /></template></q-banner>
    <q-banner v-if="!admin && !auth.isAdmin && !store.personalSettingsEnabled" class="app-surface-tonal q-mb-md">Persönliche Standardmodelle sind deaktiviert. Die gespeicherten Werte bleiben erhalten.</q-banner>
    <AppSelect v-if="showManagement && ready" v-model="profileId" outlined dense emit-value map-options label="Agentenprofil" class="q-mb-md" :options="profileOptions" :disable="store.loading || store.saving" />
    <q-banner v-if="showManagement && ready && profileUnavailable" rounded class="app-surface-tonal q-mb-md">Der angefragte Agent ist in diesem Bereich nicht verfügbar.</q-banner>
    <q-card v-if="showManagement && ready" flat class="app-surface">
      <q-card-section class="q-gutter-md">
        <ChatModelFields :model-value="modelChoice" :inherited="inheritedChoice" :disabled="blocked || profileUnavailable" @update:model-value="setChoice" />
        <template v-if="!profileId">
          <AppSelect v-for="field in fields" :key="field.key" :model-value="form[field.key] ?? null" outlined dense emit-value map-options :label="field.label" :options="options(field)" :disable="blocked" :hint="form[field.key] ? undefined : effectiveLabel(field)" @update:model-value="form[field.key] = $event" />
          <AppSelect :model-value="form.titleVisionFallbackEnabled ?? null" outlined dense emit-value map-options label="Titel bei Bildinhalten mit Visionmodell erzeugen" :options="[{ label: 'Automatische Auswahl', value: null }, { label: 'An', value: true }, { label: 'Aus', value: false }]" :hint="form.titleVisionFallbackEnabled == null ? `Effektiv: ${store.inherited.titleVisionFallbackEnabled === false ? 'Aus' : 'An'}` : undefined" :disable="blocked" @update:model-value="form.titleVisionFallbackEnabled = $event" />
        </template>
      </q-card-section>
    </q-card>
  </section>
</template>

<script setup>
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useAuthStore } from 'stores/auth'
import { useProviderStore } from 'stores/providers'
import { useConfirm } from 'src/composables/confirm'
import { useModelDefaultsStore } from './model-defaults-store'
import ChatModelFields from './ChatModelFields.vue'
import PersonalSettingsGate from './PersonalSettingsGate.vue'

const { admin } = defineProps({ admin: Boolean })
const auth = useAuthStore(), providers = useProviderStore(), store = useModelDefaultsStore(), $q = useQuasar()
const { confirm } = useConfirm()
const route = useRoute()
const section = ref(route.query.profileId ? 'management' : 'settings'), profileId = ref(route.query.profileId || null), form = ref({}), saved = ref(''), failed = ref(false), ready = ref(false)
const scope = computed(() => admin ? 'instance' : 'personal')
const showManagement = computed(() => !admin || section.value === 'management')
const blocked = computed(() => !ready.value || failed.value || store.loading || store.saving || !admin && !auth.isAdmin && !store.personalSettingsEnabled)
const dirty = computed(() => JSON.stringify(form.value) !== saved.value)
const profileOptions = computed(() => [{ label: 'Standard', value: null }, ...store.profiles.map((profile) => ({ label: profile.name, value: profile.id }))])
const profileUnavailable = computed(() => Boolean(profileId.value) && !store.profiles.some((profile) => profile.id === profileId.value))
const modelChoice = computed(() => profileId.value ? form.value.profileModels?.[profileId.value] || {} : { modelId: form.value.defaultModelId, reasoningEffort: form.value.reasoningEffort })
const inheritedChoice = computed(() => {
  const inherited = store.inherited
  if (!profileId.value) return { modelId: inherited.defaultModelId || store.automaticModelId, reasoningEffort: inherited.reasoningEffort }
  const profile = inherited.profileModels?.[profileId.value]
  return { modelId: profile?.modelId || form.value.defaultModelId || inherited.defaultModelId || store.automaticModelId, reasoningEffort: profile?.reasoningEffort || form.value.reasoningEffort || inherited.reasoningEffort }
})
const fields = [
  { key: 'compactionModelId', label: 'Komprimierung' }, { key: 'titleGenerationModelId', label: 'Titel' },
  { key: 'dreamingModelId', label: 'Memory / Dreaming', tools: true },
  { key: 'visionModelId', label: 'Bildanalyse', options: 'visionModelOptions' }, { key: 'videoModelId', label: 'Videoanalyse', options: 'videoModelOptions' },
  { key: 'audioAnalysisModelId', label: 'Audioanalyse', options: 'audioAnalysisModelOptions' },
  { key: 'imageGenerationModelId', label: 'Bilderzeugung', capability: 'image_generation' }, { key: 'speechGenerationModelId', label: 'Spracherzeugung', capability: 'speech_generation' },
  { key: 'audioGenerationModelId', label: 'Audioerzeugung', capability: 'audio_generation' }, { key: 'audioTranscriptionModelId', label: 'Transkription', capability: 'audio_transcription' }
]
const options = (field) => [{ label: 'Automatische Auswahl', value: null }, ...(field.options ? providers[field.options] : providers.selectionOptions(form.value[field.key], field.tools, field.capability || 'chat'))]
const effectiveLabel = (field) => `Effektiv: ${options(field).find((item) => item.value && item.value === (form.value[field.key] || store.inherited[field.key]))?.label || 'Auswahl zur Laufzeit'}`
function setChoice(choice) {
  if (!profileId.value) { form.value.defaultModelId = choice.modelId; form.value.reasoningEffort = choice.reasoningEffort; return }
  const overrides = form.value.profileModels ||= {}
  if (choice.modelId || choice.reasoningEffort) overrides[profileId.value] = { modelId: choice.modelId || null, reasoningEffort: choice.reasoningEffort || null }
  else delete overrides[profileId.value]
}
function assignForm() {
  form.value = JSON.parse(JSON.stringify(store.configured))
  saved.value = JSON.stringify(form.value)
}
async function load() {
  ready.value = false; failed.value = false
  try {
    if (admin) { providers.selectScope('instance'); await providers.fetchAll() } else await providers.fetchEffective()
    await store.load(scope.value)
    assignForm(); ready.value = true
  } catch { failed.value = true }
}
async function save() {
  try { await store.save(scope.value, form.value); assignForm(); $q.notify({ type: 'positive', message: 'Standardmodelle gespeichert' }) } catch { /* Central API toast. */ }
}
async function setEnabled(enabled) {
  try { await store.setPersonalEnabled(enabled); $q.notify({ type: 'positive', message: 'Einstellung gespeichert' }) } catch { /* Central API toast. */ }
}
const discard = () => !store.saving && (!ready.value || !dirty.value || confirm('Ungespeicherte Änderungen verwerfen?'))
onBeforeRouteLeave(discard)
onBeforeRouteUpdate(discard)
watch(scope, load, { immediate: true })
watch(() => route.query.profileId, (id) => { profileId.value = id || null; if (id) section.value = 'management' })
</script>
