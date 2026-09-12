<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Standardmodelle</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Modelle für Agent-Läufe und automatische Hintergrundaufgaben festlegen.
        </p>
      </div>
      <div class="settings-scope-actions row items-center col-auto">
        <SettingsScopeToggle
          :model-value="providers.scope"
          :is-admin="auth.isAdmin"
          :disable="providers.loading || saving || gateLoading"
          @update:model-value="changeScope"
        />
        <q-btn
          class="col-auto"
          color="primary"
          unelevated
          no-caps
          no-wrap
          icon="save"
          label="Speichern"
          :loading="saving"
          :disable="personalBlocked || !loaded || providers.loading || !changed"
          @click="save"
        />
      </div>
    </header>

    <q-card v-if="auth.isAdmin && providers.scope === 'instance'" flat class="app-surface-tonal q-mb-md">
      <q-card-section class="row items-center justify-between q-gutter-md">
        <div>
          <div class="text-subtitle2 text-weight-bold">Persönliche Provider und Standardmodelle</div>
          <div class="text-caption app-muted">Erlaubt benutzereigene Provider und Modellvorgaben.</div>
        </div>
        <q-toggle
          :model-value="providers.personalProvidersEnabled"
          color="primary"
          label="Erlaubt"
          :disable="gateLoading"
          @update:model-value="togglePersonalProviders"
        />
      </q-card-section>
    </q-card>

    <q-banner v-if="providers.scope === 'personal'" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="person" color="primary" /></template>
      {{ personalBlocked
        ? 'Persönliche Provider-Einstellungen wurden für diese Instanz deaktiviert. Gespeicherte Vorgaben bleiben erhalten, können aber nicht bearbeitet werden.'
        : 'Persönliche Standardmodelle überschreiben die Instanzvorgaben, wenn das gewählte Modell verfügbar ist.' }}
    </q-banner>

    <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Standardmodelle konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <q-banner v-else-if="loaded && !providers.modelOptions.length" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="warning_amber" color="warning" /></template>
      Es ist derzeit kein effektiv verfügbares Chatmodell eingerichtet. Aktiviere zuerst einen Provider und mindestens ein Chatmodell.
    </q-banner>

    <q-card
      flat
      class="app-surface default-models-card"
      :class="{ disabled: personalBlocked }"
      :inert="personalBlocked"
    >
      <template v-for="(setting, index) in modelSettings" :key="setting.field">
        <q-separator v-if="index" />

        <q-card-section class="default-model-setting">
          <q-avatar rounded :color="setting.color" text-color="white" :icon="setting.icon" />
          <div class="default-model-setting__description">
            <div class="text-subtitle1 text-weight-bold">{{ setting.title }}</div>
            <div class="text-body2 app-muted">{{ setting.description }}</div>
          </div>
          <div>
            <q-select
              v-model="form[setting.field]"
              outlined
              clearable
              emit-value
              map-options
              options-dense
              :label="setting.label"
              :display-value="form[setting.field] ? undefined : fallbackLabel(setting)"
              :options="settingOptions(setting)"
              :disable="personalBlocked"
              :hint="setting.hint"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
            />
            <q-checkbox
              v-if="setting.titleVisionFallback"
              v-model="form.titleVisionFallbackEnabled"
              class="q-mt-sm"
              color="primary"
              label="Fallback auf Vision-Modell bei Bildinhalten"
              :disable="personalBlocked"
            />
          </div>
        </q-card-section>
      </template>

      <q-inner-loading :showing="providers.loading && !loaded" />
    </q-card>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useAuthStore } from 'stores/auth'
import { useProviderStore } from 'stores/providers'

defineOptions({ name: 'DefaultModelSettingsPage' })

const $q = useQuasar()
const providers = useProviderStore()
const auth = useAuthStore()
const loaded = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const gateLoading = ref(false)
const modelSettings = [
  {
    field: 'defaultModelId',
    requiresTools: true,
    title: 'Agent',
    description: 'Standard für neue Sessions, solange dort kein eigenes Modell gewählt wurde.',
    label: 'Agentmodell',
    fallback: 'Kein Standardmodell',
    hint: 'Agent-Läufe benötigen ein Modell mit Tool-Unterstützung.',
    icon: 'smart_toy',
    color: 'primary'
  },
  {
    field: 'skillCreationModelId',
    options: 'skillCreationModelOptions',
    title: 'Skill-Erstellung',
    description: 'Erstellt aus einer Session einen prüfbaren Skill-Entwurf.',
    label: 'Modell für Skill-Erstellung',
    fallback: 'Sessionmodell verwenden',
    hint: 'Nur verfügbare Chatmodelle mit Tool-Unterstützung.',
    icon: 'auto_awesome',
    color: 'indigo'
  },
  {
    field: 'videoModelId',
    options: 'videoModelOptions',
    title: 'Videoanalyse',
    description: 'Wird verwendet, wenn es gegenüber dem aktiven Modell zusätzliche Video- oder Bildfähigkeiten bietet.',
    label: 'Videomodell',
    fallback: 'Kein Standardmodell',
    hint: 'Das aktive Modell bleibt bevorzugt, wenn es dieselbe oder eine bessere Eingabefähigkeit besitzt.',
    icon: 'video_file',
    color: 'purple'
  },
  {
    field: 'visionModelId',
    options: 'visionModelOptions',
    title: 'Bildanalyse',
    description: 'Liest bildbasierte PDF-Seiten, wenn das aktive Agentmodell keine Bilder verarbeiten kann.',
    label: 'Visionmodell',
    fallback: 'Kein Standardmodell',
    hint: 'Nur verfügbare Chatmodelle mit Bildeingabe.',
    icon: 'document_scanner',
    color: 'blue-grey'
  },
  {
    field: 'compactionModelId',
    title: 'Kontextkomprimierung',
    description: 'Verdichtet ältere Gesprächsinhalte, wenn das Kontextfenster knapp wird.',
    label: 'Komprimierungsmodell',
    fallback: 'Sessionmodell verwenden',
    icon: 'compress',
    color: 'secondary'
  },
  {
    field: 'titleGenerationModelId',
    titleVisionFallback: true,
    title: 'Titelgenerierung',
    description: 'Erstellt parallel zum ersten Agent-Run einen kurzen Titel aus der ersten Nachricht und ihren Anhängen.',
    label: 'Modell für Titel',
    icon: 'title',
    color: 'accent'
  },
  {
    field: 'dreamingModelId',
    requiresTools: true,
    title: 'Dreaming',
    description: 'Verarbeitet abgeschlossene Gespräche im Hintergrund zu langfristigen Erinnerungen.',
    label: 'Dreaming-Modell',
    fallback: 'Agentmodell verwenden',
    hint: 'Das optionale Dreaming benötigt ein Modell mit Tool-Unterstützung.',
    icon: 'bedtime',
    color: 'deep-purple'
  },
  {
    field: 'imageGenerationModelId',
    capability: 'image_generation',
    title: 'Bildgenerierung',
    description: 'Erzeugt Bilder aus einer textlichen Beschreibung.',
    label: 'Bildmodell',
    fallback: 'Kein Standardmodell',
    icon: 'image',
    color: 'teal'
  },
  {
    field: 'speechGenerationModelId',
    capability: 'speech_generation',
    title: 'Sprachausgabe',
    description: 'Wandelt Text in gesprochene Sprache um.',
    label: 'Sprachmodell',
    fallback: 'Kein Standardmodell',
    icon: 'record_voice_over',
    color: 'deep-orange'
  },
  {
    field: 'audioGenerationModelId',
    capability: 'audio_generation',
    title: 'Audio und Musik',
    description: 'Erzeugt Klänge, Audio oder Musik aus einer Beschreibung.',
    label: 'Audiomodell',
    fallback: 'Kein Standardmodell',
    icon: 'music_note',
    color: 'indigo'
  },
  {
    field: 'audioTranscriptionModelId',
    capability: 'audio_transcription',
    title: 'Transkription',
    description: 'Wandelt Sprache und andere Audioaufnahmen in Text um.',
    label: 'Transkriptionsmodell',
    fallback: 'Kein Standardmodell',
    icon: 'graphic_eq',
    color: 'cyan-8'
  }
]
const form = reactive({
  defaultModelId: null,
  compactionModelId: null,
  titleGenerationModelId: null,
  titleVisionFallbackEnabled: true,
  dreamingModelId: null,
  visionModelId: null,
  videoModelId: null,
  imageGenerationModelId: null,
  speechGenerationModelId: null,
  audioGenerationModelId: null,
  audioTranscriptionModelId: null,
  skillCreationModelId: null
})
const formKeys = [...modelSettings.map((setting) => setting.field), 'titleVisionFallbackEnabled']
const changed = computed(() => formKeys.some((key) => form[key] !== providers[key]))
const personalBlocked = computed(() => (
  providers.scope === 'personal' && !providers.personalProvidersEnabled
))
const titleGenerationFallbackLabel = computed(() => {
  if (providers.scope !== 'personal') return 'Sessionmodell verwenden'
  if (!providers.effectiveTitleGenerationModelId) return 'Instanzvorgabe: Sessionmodell verwenden'
  return `Instanzvorgabe: ${providers.selectedModelState(providers.effectiveTitleGenerationModelId).label}`
})

function assignForm() {
  for (const key of formKeys) form[key] = providers[key]
}

function settingOptions(setting) {
  if (setting.options) return providers[setting.options]
  if (setting.capability) return providers.capabilityOptions(setting.capability)
  return providers.selectionOptions(form[setting.field], setting.requiresTools)
}

function fallbackLabel(setting) {
  return setting.titleVisionFallback ? titleGenerationFallbackLabel.value : setting.fallback
}

async function load() {
  loadFailed.value = false
  try {
    await providers.fetchAll()
    assignForm()
    loaded.value = true
  } catch {
    loadFailed.value = true
  }
}

async function changeScope(scope) {
  providers.selectScope(scope)
  loaded.value = false
  await load()
}

async function togglePersonalProviders(enabled) {
  gateLoading.value = true
  try {
    await providers.setPersonalProvidersEnabled(enabled)
  } finally {
    gateLoading.value = false
  }
}

async function save() {
  if (!loaded.value || !changed.value) return
  saving.value = true
  try {
    await providers.setDefaultModels({ ...form })
    assignForm()
    $q.notify({ type: 'positive', message: 'Standardmodelle gespeichert' })
  } finally {
    saving.value = false
  }
}

onMounted(() => changeScope('personal'))
</script>
