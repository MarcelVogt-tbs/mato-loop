<template>
  <section :aria-busy="agents.loading">
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
      <q-btn flat round dense icon="arrow_back" aria-label="Zurück zu den Agentenprofilen" :disable="agents.saving" :to="{ name: listRoute }" />
      <div class="col q-px-sm">
        <div class="text-h6 text-weight-bold">{{ isCreate ? 'Profil erstellen' : editingAgent?.name || 'Profil bearbeiten' }}</div>
        <div class="text-body2 app-muted">Aufgabe und Arbeitsweise des Agenten.</div>
      </div>
      <q-btn v-if="!accessProtected" unelevated no-caps color="primary" icon="save" label="Speichern" :loading="agents.saving" :disable="blocked || loadFailed || !dirty" @click="saveAgent" />
    </q-toolbar>
    <q-banner v-if="blocked" rounded class="app-surface-tonal q-mb-md">Persönliche Agenten wurden für diese Instanz deaktiviert.</q-banner>
    <q-banner v-else-if="loadFailed" rounded class="app-surface-tonal q-mb-md">
      {{ notFound ? 'Das Agentenprofil wurde nicht gefunden.' : 'Das Agentenprofil konnte nicht geladen werden.' }}
      <template #action><q-btn flat no-caps color="primary" label="Erneut laden" @click="load" /></template>
    </q-banner>
    <q-form v-else ref="formRef" @submit.prevent="saveAgent">
      <q-tabs v-model="section" dense no-caps align="left" active-color="primary">
        <q-tab name="profile" icon="badge" label="Profil" />
        <q-tab name="models" icon="stars" label="Modelle" />
        <q-tab name="access" icon="tune" label="Weitere Einstellungen" />
      </q-tabs>
      <q-tab-panels v-model="section" class="bg-transparent">
        <q-tab-panel name="profile" class="q-pa-none">
          <q-card flat class="app-surface q-mt-md">
            <q-card-section class="q-gutter-md">
              <q-input dense v-model="form.name" outlined maxlength="80" label="Name" :disable="isProtected" :rules="[requiredRule]" />
              <q-input dense v-model="form.description" outlined maxlength="500" type="textarea" autogrow label="Beschreibung" :disable="isProtected" />
              <q-select dense v-if="!isProtected" v-model="form.baseProfileId" outlined emit-value map-options label="Basisprofil" :options="baseProfileOptions" @update:model-value="applyInheritedValues" />
              <div v-if="!isProtected" class="text-caption app-muted">Nicht angepasste Einstellungen werden vom Basisprofil übernommen.</div>
              <template v-for="field in textFields" :key="field.key">
                <q-btn-toggle v-if="!isProtected" :model-value="ownership[field.key] ? 'own' : 'inherit'" unelevated no-caps toggle-color="primary" :options="inheritOptions" role="group" :aria-label="`${field.label} übernehmen oder anpassen`" @update:model-value="setOwnership(field.key, $event)" />
                <q-input dense v-model="form[field.key]" outlined type="textarea" autogrow :label="field.label" :maxlength="field.max" :disable="isProtected || !ownership[field.key]" :rules="field.key === 'instructions' && ownership.instructions ? [requiredRule] : []" />
              </template>
               <q-select dense :model-value="modelValue('answerDisplay')" outlined emit-value map-options label="Antwortdarstellung" :options="inheritedOptions('answerDisplay', answerDisplayOptions)" :hint="answerDisplayOptions.find((option) => option.value === form.answerDisplay)?.hint" persistent-hint :disable="isProtected" @update:model-value="setModelValue('answerDisplay', $event)" />
              <q-btn-toggle v-if="!isProtected" :model-value="ownership.maxToolCalls ? 'own' : 'inherit'" unelevated no-caps toggle-color="primary" :options="inheritOptions" @update:model-value="setOwnership('maxToolCalls', $event)" />
              <q-checkbox dense :model-value="form.maxToolCalls === null" label="Tool-Aufrufe unbegrenzt" :disable="isProtected || !ownership.maxToolCalls" @update:model-value="form.maxToolCalls = $event ? null : 25" />
              <q-input dense v-if="form.maxToolCalls !== null" v-model.number="form.maxToolCalls" outlined type="number" min="1" label="Maximale Tool-Aufrufe" :disable="isProtected || !ownership.maxToolCalls" :rules="[toolLimitRule]" />
              <div class="text-caption app-muted">Unbegrenzte Aufrufe können Modell- und Dienstkosten erhöhen.</div>
               <q-select dense v-for="field in booleanFields" :key="field.key" :model-value="modelValue(field.key)" outlined emit-value map-options :label="field.label" :options="inheritedOptions(field.key, [{ label: 'An', value: true }, { label: 'Aus', value: false }])" :disable="isProtected" @update:model-value="setModelValue(field.key, $event)" />
              <q-btn-toggle v-if="!isProtected" :model-value="ownership.sessionWritePaths ? 'own' : 'inherit'" unelevated no-caps toggle-color="primary" :options="inheritOptions" @update:model-value="setOwnership('sessionWritePaths', $event)" />
              <q-select dense v-model="form.sessionWriteMode" outlined emit-value map-options label="Schreiben in die Session-Ablage" :options="sessionWriteModeOptions" :disable="isProtected || !ownership.sessionWritePaths" />
              <q-input dense v-if="form.sessionWriteMode === 'paths'" v-model="form.sessionWritePathsText" outlined type="textarea" autogrow label="Erlaubte relative Pfade" :disable="isProtected || !ownership.sessionWritePaths" :rules="[sessionWritePathsRule]" />
            </q-card-section>
          </q-card>
        </q-tab-panel>
        <q-tab-panel name="models" class="q-pa-none">
          <q-card flat class="app-surface q-mt-md">
            <q-card-section class="q-gutter-md">
              <q-select dense :model-value="modelValue('defaultModelId')" outlined clearable emit-value map-options label="Agentmodell" :options="modelOptions({ field: 'defaultModelId', requiresTools: true })" :disable="accessProtected" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" hint="Benötigt Tool-Unterstützung. Ohne Auswahl kann dieses Profil keine Agentenläufe starten." @update:model-value="setModelValue('defaultModelId', $event)" />
              <q-select dense :model-value="modelValue('reasoningEffort')" outlined emit-value map-options label="Reasoning-Stufe" :options="reasoningSelectionOptions" :disable="accessProtected" @update:model-value="setModelValue('reasoningEffort', $event)" />
              <q-select dense v-for="setting in additionalModelSettings" :key="setting.field" :model-value="modelValue(setting.field)" outlined clearable emit-value map-options :label="setting.label" :options="modelOptions(setting)" :disable="accessProtected" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" @update:model-value="setModelValue(setting.field, $event)" />
              <q-select dense :model-value="modelValue('titleVisionFallbackEnabled')" outlined emit-value map-options label="Bei Bildinhalten auf das Visionmodell ausweichen" :options="titleVisionFallbackOptions" :disable="accessProtected" @update:model-value="setModelValue('titleVisionFallbackEnabled', $event)" />
            </q-card-section>
          </q-card>
        </q-tab-panel>
        <q-tab-panel name="access" class="q-pa-none">
          <p class="text-body2 app-muted q-mt-md">Diese Einstellungen werden in ihren jeweiligen Verwaltungsbereichen gepflegt.</p>
          <q-banner v-if="isCreate" rounded class="app-surface-tonal q-mb-md">Speichere den Agenten zuerst, um seine Persona und Rechte zu öffnen.</q-banner>
          <q-card flat class="app-surface">
            <q-card-section class="row items-center q-col-gutter-md">
              <q-icon name="face" color="primary" size="sm" class="col-auto" />
              <div class="col">
                <div class="text-subtitle1 text-weight-bold">Persona</div>
                <div class="text-body2 app-muted">{{ editingAgent?.resolvedPersonaName || 'Globale Persona' }}</div>
              </div>
              <div class="col-12 col-sm-auto">
                <q-btn flat no-caps color="primary" label="Persona öffnen" :disable="!editingAgent || agents.loading || agents.saving" :to="{ name: admin ? 'admin-personas' : 'settings-personalization', query: { ...(!admin ? { tab: 'assistant' } : {}), profileId: editingAgent?.id } }" />
              </div>
            </q-card-section>
            <q-separator />
            <q-card-section class="row items-center q-col-gutter-md">
              <q-icon name="build" color="primary" size="sm" class="col-auto" />
              <div class="col">
                <div class="text-subtitle1 text-weight-bold">Tool-Rechte</div>
                <div class="text-body2 app-muted">{{ inheritedAccess ? `Vorgaben des Basisprofils „${accessProfileName}“.` : 'Berechtigungen und abweichende Regeln dieses Agenten.' }}</div>
              </div>
              <div class="col-12 col-sm-auto">
                <q-btn flat no-caps color="primary" label="Tool-Rechte öffnen" :disable="!accessProfileId || agents.loading || agents.saving" :to="{ name: admin ? 'admin-tools' : 'settings-tools', query: { profileId: accessProfileId } }" />
              </div>
            </q-card-section>
            <q-separator />
            <q-card-section class="row items-center q-col-gutter-md">
              <q-icon name="extension" color="primary" size="sm" class="col-auto" />
              <div class="col">
                <div class="text-subtitle1 text-weight-bold">Integrationen</div>
                <div class="text-body2 app-muted">{{ inheritedAccess ? `Verbindungszuordnungen des Basisprofils „${accessProfileName}“.` : 'Welche Verbindungen dieser Agent verwenden darf.' }}</div>
              </div>
              <div class="col-12 col-sm-auto">
                <q-btn flat no-caps color="primary" label="Zuordnungen öffnen" :disable="!accessProfileId || agents.loading || agents.saving" :to="{ name: admin ? 'admin-integrations' : 'settings-integrations', query: { profileId: accessProfileId } }" />
              </div>
            </q-card-section>
          </q-card>
        </q-tab-panel>
      </q-tab-panels>
    </q-form>
    <q-inner-loading :showing="agents.loading" />
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { modelReasoningEffortOptions } from 'src/utils/model-runtime'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useAuthStore } from 'stores/auth'
import { useProviderStore } from 'stores/providers'

defineOptions({ name: 'AgentProfileEditorPage' })
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const agents = useAgentProfileStore()
const auth = useAuthStore()
const providers = useProviderStore()
const formRef = ref(null)
const editingAgent = ref(null)
const loadFailed = ref(false)
const notFound = ref(false)
const savedPayload = ref('')
const section = ref('profile')
let loadToken = 0
const admin = computed(() => String(route.name).startsWith('admin-'))
const scope = computed(() => admin.value ? 'instance' : 'personal')
const listRoute = computed(() => admin.value ? 'admin-agents' : 'settings-agents')
const isCreate = computed(() => ['settings-agent-create', 'admin-agent-create'].includes(route.name))
const isProtected = computed(() => editingAgent.value?.protected === true)
const inheritedAccess = computed(() => admin.value && editingAgent.value && !isProtected.value)
const accessProfileId = computed(() => inheritedAccess.value ? editingAgent.value.baseProfileId : editingAgent.value?.id)
const accessProfileName = computed(() => agents.baseProfiles.find((profile) => profile.id === accessProfileId.value)?.name || 'Basisprofil')
const accessProtected = computed(() => admin.value && !auth.isAdmin || isProtected.value && !editingAgent.value?.accessEditable)
const blocked = computed(() => !admin.value && !auth.isAdmin && !agents.personalAgentsEnabled)
const inheritOptions = [{ label: 'Erben', value: 'inherit' }, { label: 'Eigener Wert', value: 'own' }]
const INHERIT_MODEL_VALUE = '__inherit__'
const textFields = [{ key: 'instructions', label: 'Anweisungen', max: 16000 }, { key: 'runtimeInstructions', label: 'Laufzeit-Hinweis', max: 4000 }]
const booleanFields = [{ key: 'todoEnabled', label: 'Session-Aufgaben' }, { key: 'delegationEnabled', label: 'Delegation erlauben' }]
const answerDisplayOptions = [
  { label: 'Ablauf', value: 'timeline', hint: 'Zeigt alle Arbeitsschritte direkt in zeitlicher Reihenfolge.' },
  { label: 'Kompakt', value: 'compact', hint: 'Zeigt die aktuelle Aktivität sofort und den bisherigen Verlauf auf Wunsch.' }
]
const sessionWriteModeOptions = [{ label: 'Unbeschränkt', value: 'unrestricted' }, { label: 'Alles verweigern', value: 'denied' }, { label: 'Nur angegebene Pfade', value: 'paths' }]
const additionalModelSettings = [
  { field: 'compactionModelId', label: 'Komprimierungsmodell' },
  { field: 'titleGenerationModelId', label: 'Titelmodell' },
  { field: 'skillCreationModelId', label: 'Modell für Skill-Erstellung', requiresTools: true },
  { field: 'dreamingModelId', label: 'Dreaming-Modell', requiresTools: true },
  { field: 'visionModelId', label: 'Visionmodell', options: 'vision' },
  { field: 'videoModelId', label: 'Videomodell', options: 'video' },
  { field: 'audioAnalysisModelId', label: 'Audioanalyse-Modell', options: 'audio' },
  { field: 'imageGenerationModelId', label: 'Bildmodell', capability: 'image_generation' },
  { field: 'speechGenerationModelId', label: 'Sprachmodell', capability: 'speech_generation' },
  { field: 'audioGenerationModelId', label: 'Audiomodell', capability: 'audio_generation' },
  { field: 'audioTranscriptionModelId', label: 'Transkriptionsmodell', capability: 'audio_transcription' }
]
const modelFields = ['defaultModelId', 'reasoningEffort', 'titleVisionFallbackEnabled', ...additionalModelSettings.map((item) => item.field)]
const scalarFields = ['instructions', 'runtimeInstructions', 'answerDisplay', 'maxToolCalls', 'todoEnabled', 'delegationEnabled', ...modelFields]
const form = reactive({ name: '', description: '', baseProfileId: null, instructions: '', runtimeInstructions: '', answerDisplay: 'compact', maxToolCalls: 25, todoEnabled: false, delegationEnabled: false, ...Object.fromEntries(modelFields.map((field) => [field, null])), titleVisionFallbackEnabled: true, sessionWriteMode: 'unrestricted', sessionWritePathsText: '' })
const ownership = reactive(Object.fromEntries([...scalarFields, 'sessionWritePaths'].map((field) => [field, false])))
const baseProfiles = computed(() => agents.baseProfiles)
const baseProfileOptions = computed(() => baseProfiles.value.map((agent) => ({ label: agent.name, value: agent.id })))
const baseProfile = computed(() => editingAgent.value?.accessBaseline || baseProfiles.value.find((agent) => agent.id === form.baseProfileId))
const selectedAgentModel = computed(() => (providers.availableModels || providers.models).find((model) => model.id === form.defaultModelId))
const reasoningOptions = computed(() => modelReasoningEffortOptions(selectedAgentModel.value))
const reasoningSelectionOptions = computed(() => [{ label: 'Erben: Providerstandard', value: INHERIT_MODEL_VALUE }, ...reasoningOptions.value])
const titleVisionFallbackOptions = computed(() => [{ label: `Erben: ${baseProfile.value?.titleVisionFallbackEnabled === false ? 'Deaktiviert' : 'Aktiviert'}`, value: INHERIT_MODEL_VALUE }, { label: 'Aktiviert', value: true }, { label: 'Deaktiviert', value: false }])
const dirty = computed(() => !accessProtected.value && Boolean(savedPayload.value) && JSON.stringify(requestPayload()) !== savedPayload.value)
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const toolLimitRule = (value) => Number.isSafeInteger(value) && value > 0 || 'Eine positive ganze Zahl eingeben.'
const sessionWritePaths = () => form.sessionWritePathsText.split('\n').map((path) => path.trim()).filter(Boolean)
const sessionWritePathsRule = () => form.sessionWriteMode !== 'paths' || sessionWritePaths().length > 0 && sessionWritePaths().every((path) => !path.startsWith('/') && !path.endsWith('/') && !path.includes('\\') && !path.includes('//') && !path.split('/').some((part) => part === '.' || part === '..') && !/[*?[\]{}]/.test(path)) || 'Mindestens einen normalisierten relativen POSIX-Pfad eingeben.'

function requestPayload() {
  const overrides = Object.fromEntries(scalarFields.filter((field) => ownership[field]).map((field) => [field, typeof form[field] === 'string' ? form[field].trim() : form[field]]))
  if (ownership.sessionWritePaths) overrides.sessionWritePaths = form.sessionWriteMode === 'unrestricted' ? null : form.sessionWriteMode === 'denied' ? [] : sessionWritePaths()
  return { name: form.name.trim(), description: form.description.trim(), baseProfileId: form.baseProfileId, overrides }
}
function assignValues(agent) {
  for (const field of scalarFields) form[field] = agent?.[field] ?? ({ instructions: '', runtimeInstructions: '', answerDisplay: 'compact', maxToolCalls: 25, todoEnabled: false, delegationEnabled: false, titleVisionFallbackEnabled: true }[field] ?? null)
  if (agent?.maxToolCalls === null) form.maxToolCalls = null
  const paths = agent?.sessionWritePaths
  form.sessionWriteMode = paths == null ? 'unrestricted' : paths.length ? 'paths' : 'denied'
  form.sessionWritePathsText = paths?.join('\n') || ''
}
function applyInheritedValues() {
  if (!baseProfile.value) return
  const current = Object.fromEntries(scalarFields.filter((field) => ownership[field]).map((field) => [field, form[field]]))
  const paths = { sessionWriteMode: form.sessionWriteMode, sessionWritePathsText: form.sessionWritePathsText }
  assignValues(baseProfile.value)
  Object.assign(form, current, ownership.sessionWritePaths ? paths : {})
}
function setOwnership(field, value) { ownership[field] = value === 'own'; applyInheritedValues() }
function modelValue(field) { return ownership[field] ? form[field] : INHERIT_MODEL_VALUE }
function setModelValue(field, value) {
  ownership[field] = value !== INHERIT_MODEL_VALUE
  if (ownership[field]) form[field] = value
  else applyInheritedValues()
  if (field === 'defaultModelId' && ownership.reasoningEffort && !selectedAgentModel.value?.reasoningEfforts?.includes(form.reasoningEffort)) form.reasoningEffort = null
}
function modelOptions(setting) {
  const options = setting.options === 'vision' ? providers.visionModelOptions : setting.options === 'video' ? providers.videoModelOptions : setting.options === 'audio' ? providers.audioAnalysisModelOptions : providers.selectionOptions(form[setting.field], setting.requiresTools === true, setting.capability || 'chat')
  return inheritedOptions(setting.field, options, 'Kein Modell')
}
function inheritedOptions(field, options, emptyLabel = 'Standard') {
  const inherited = baseProfile.value?.[field]
  return [{ label: `Erben: ${options.find((item) => item.value === inherited)?.label || (inherited ? 'Nicht verfügbar' : emptyLabel)}`, value: INHERIT_MODEL_VALUE }, ...options]
}
function assignForm(agent = null) {
  form.name = agent?.name || ''
  form.description = agent?.description || ''
  form.baseProfileId = agent?.baseProfileId || baseProfiles.value[0]?.id
  for (const field of Object.keys(ownership)) ownership[field] = Object.hasOwn(agent?.overrides || {}, field)
  assignValues(agent || baseProfile.value)
  savedPayload.value = JSON.stringify(requestPayload())
}
async function load() {
  const token = ++loadToken
  editingAgent.value = null
  savedPayload.value = ''
  loadFailed.value = false
  notFound.value = false
  agents.selectScope(scope.value)
  providers.selectScope(scope.value)
  try {
    await Promise.all([agents.fetchAgents(scope.value), auth.isAdmin ? providers.fetchAll() : providers.fetchEffective()])
    if (token !== loadToken) return
    if (blocked.value) return
    if (!isCreate.value) {
      editingAgent.value = agents.byId(String(route.params.agentId || ''))
      if (!editingAgent.value || editingAgent.value.scope !== scope.value) { notFound.value = true; loadFailed.value = true; return }
    }
    assignForm(editingAgent.value)
  } catch { if (token === loadToken) loadFailed.value = true }
}
async function saveAgent() {
  if (accessProtected.value || blocked.value || agents.loading || agents.saving || loadFailed.value || !dirty.value) return
  const invalid = ownership.maxToolCalls && form.maxToolCalls !== null && toolLimitRule(form.maxToolCalls) !== true || ownership.sessionWritePaths && sessionWritePathsRule() !== true
  if (invalid || !form.name.trim() || ownership.instructions && !form.instructions.trim()) { section.value = 'profile'; await nextTick() }
  if (!await formRef.value?.validate() || invalid) return
  try {
    const creating = isCreate.value
    const saved = editingAgent.value ? await agents.updateAgent(editingAgent.value.id, requestPayload()) : await agents.createAgent(requestPayload())
    editingAgent.value = saved
    assignForm(saved)
    if (creating) await router.replace({ name: admin.value ? 'admin-agent-edit' : 'settings-agent-edit', params: { agentId: saved.id } })
    $q.notify({ type: 'positive', message: creating ? 'Agent erstellt' : 'Agent gespeichert' })
  } catch { /* Central API toast. */ }
}
async function confirmDiscardedChanges() {
  if (agents.saving) return false
  return !dirty.value || confirm('Sollen die ungespeicherten Änderungen verworfen werden?', 'Änderungen verwerfen?', { okLabel: 'Verwerfen' })
}
onMounted(load)
watch(() => [route.name, route.params.agentId], load)
onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
