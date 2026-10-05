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
    <q-card v-if="showManagement && ready" flat class="app-surface">
      <q-card-section class="q-gutter-md">
        <ChatModelFields :model-value="{ modelId: form.defaultModelId, reasoningEffort: form.reasoningEffort }" :inherited="{ modelId: store.inherited.defaultModelId || store.automaticModelId, reasoningEffort: store.inherited.reasoningEffort }" :automatic="!store.inherited.defaultModelId" :disabled="blocked" @update:model-value="form.defaultModelId = $event.modelId; form.reasoningEffort = $event.reasoningEffort" />
        <q-select v-for="field in fields" :key="field.key" v-model="form[field.key]" outlined dense clearable emit-value map-options :label="field.label" :options="options(field)" :disable="blocked" :hint="inheritedLabel(field)" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" />
        <q-select v-model="form.titleVisionFallbackEnabled" outlined dense emit-value map-options label="Titel bei Bildinhalten mit Visionmodell erzeugen" :options="[{ label: `Vorgabe (${store.inherited.titleVisionFallbackEnabled === false ? 'Aus' : 'An'})`, value: null }, { label: 'An', value: true }, { label: 'Aus', value: false }]" :disable="blocked" />
      </q-card-section>
    </q-card>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { useAuthStore } from 'stores/auth'
import { useProviderStore } from 'stores/providers'
import { useConfirm } from 'src/composables/confirm'
import { useModelDefaultsStore } from './model-defaults-store'
import ChatModelFields from './ChatModelFields.vue'
import PersonalSettingsGate from './PersonalSettingsGate.vue'

const { admin } = defineProps({ admin: Boolean })
const auth = useAuthStore(), providers = useProviderStore(), store = useModelDefaultsStore(), $q = useQuasar()
const { confirm } = useConfirm()
const section = ref('settings'), form = ref({}), saved = ref(''), failed = ref(false), ready = ref(false)
const scope = computed(() => admin ? 'instance' : 'personal')
const showManagement = computed(() => !admin || section.value === 'management')
const blocked = computed(() => !ready.value || failed.value || store.loading || store.saving || !admin && !auth.isAdmin && !store.personalSettingsEnabled)
const dirty = computed(() => JSON.stringify(form.value) !== saved.value)
const fields = [
  { key: 'compactionModelId', label: 'Komprimierung' }, { key: 'titleGenerationModelId', label: 'Titel' },
  { key: 'skillCreationModelId', label: 'Skill-Erstellung', tools: true }, { key: 'dreamingModelId', label: 'Memory / Dreaming', tools: true },
  { key: 'visionModelId', label: 'Bildanalyse', options: 'visionModelOptions' }, { key: 'videoModelId', label: 'Videoanalyse', options: 'videoModelOptions' },
  { key: 'audioAnalysisModelId', label: 'Audioanalyse', options: 'audioAnalysisModelOptions' },
  { key: 'imageGenerationModelId', label: 'Bilderzeugung', capability: 'image_generation' }, { key: 'speechGenerationModelId', label: 'Spracherzeugung', capability: 'speech_generation' },
  { key: 'audioGenerationModelId', label: 'Audioerzeugung', capability: 'audio_generation' }, { key: 'audioTranscriptionModelId', label: 'Transkription', capability: 'audio_transcription' }
]
const options = (field) => field.options ? providers[field.options] : providers.selectionOptions(form.value[field.key], field.tools, field.capability || 'chat')
const inheritedLabel = (field) => `Vorgabe: ${options(field).find((item) => item.value === store.inherited[field.key])?.label || 'Automatische Auswahl'}`
async function load() {
  ready.value = false; failed.value = false
  try {
    if (admin) { providers.selectScope('instance'); await providers.fetchAll() } else await providers.fetchEffective()
    await store.load(scope.value)
    form.value = { ...store.configured }; saved.value = JSON.stringify(form.value); ready.value = true
  } catch { failed.value = true }
}
async function save() {
  try { await store.save(scope.value, form.value); form.value = { ...store.configured }; saved.value = JSON.stringify(form.value); $q.notify({ type: 'positive', message: 'Standardmodelle gespeichert' }) } catch { /* Central API toast. */ }
}
async function setEnabled(enabled) {
  try { await store.setPersonalEnabled(enabled); $q.notify({ type: 'positive', message: 'Einstellung gespeichert' }) } catch { /* Central API toast. */ }
}
const discard = () => !store.saving && (!ready.value || !dirty.value || confirm('Ungespeicherte Änderungen verwerfen?'))
onBeforeRouteLeave(discard)
onBeforeRouteUpdate(discard)
watch(scope, load, { immediate: true })
</script>
