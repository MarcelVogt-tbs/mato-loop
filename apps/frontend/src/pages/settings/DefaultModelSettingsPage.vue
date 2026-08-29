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
      <q-card-section class="default-model-setting">
        <q-avatar rounded color="primary" text-color="white" icon="smart_toy" />
        <div class="default-model-setting__description">
          <div class="text-subtitle1 text-weight-bold">Agent</div>
          <div class="text-body2 app-muted">
            Standard für neue Sessions, solange dort kein eigenes Modell gewählt wurde.
          </div>
        </div>
        <div>
          <q-select
            v-model="form.defaultModelId"
            outlined
            clearable
            emit-value
            map-options
            options-dense
            label="Agentmodell"
            :display-value="form.defaultModelId ? undefined : 'Kein Standardmodell'"
            :options="providers.selectionOptions(form.defaultModelId, true)"
            :disable="personalBlocked"
            hint="Agent-Läufe benötigen ein Modell mit Tool-Unterstützung."
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
          />
        </div>
      </q-card-section>

      <q-separator />

      <q-card-section class="default-model-setting">
        <q-avatar rounded color="indigo" text-color="white" icon="auto_awesome" />
        <div class="default-model-setting__description">
          <div class="text-subtitle1 text-weight-bold">Skill-Erstellung</div>
          <div class="text-body2 app-muted">
            Erstellt aus einer Session einen prüfbaren Skill-Entwurf.
          </div>
        </div>
        <div>
          <q-select
            v-model="form.skillCreationModelId"
            outlined
            clearable
            emit-value
            map-options
            options-dense
            label="Modell für Skill-Erstellung"
            :display-value="form.skillCreationModelId ? undefined : 'Sessionmodell verwenden'"
            :options="providers.skillCreationModelOptions"
            :disable="personalBlocked"
            hint="Nur verfügbare Chatmodelle mit Tool-Unterstützung."
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
          />
        </div>
      </q-card-section>

      <q-separator />

      <q-card-section class="default-model-setting">
        <q-avatar rounded color="purple" text-color="white" icon="video_file" />
        <div class="default-model-setting__description">
          <div class="text-subtitle1 text-weight-bold">Videoanalyse</div>
          <div class="text-body2 app-muted">
            Wird verwendet, wenn es gegenüber dem aktiven Modell zusätzliche Video- oder Bildfähigkeiten bietet.
          </div>
        </div>
        <div>
          <q-select
            v-model="form.videoModelId"
            outlined
            clearable
            emit-value
            map-options
            options-dense
            label="Videomodell"
            :display-value="form.videoModelId ? undefined : 'Kein Standardmodell'"
            :options="providers.videoModelOptions"
            :disable="personalBlocked"
            hint="Das aktive Modell bleibt bevorzugt, wenn es dieselbe oder eine bessere Eingabefähigkeit besitzt."
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
          />
        </div>
      </q-card-section>

      <q-separator />

      <q-card-section class="default-model-setting">
        <q-avatar rounded color="blue-grey" text-color="white" icon="document_scanner" />
        <div class="default-model-setting__description">
          <div class="text-subtitle1 text-weight-bold">Bildanalyse</div>
          <div class="text-body2 app-muted">
            Liest bildbasierte PDF-Seiten, wenn das aktive Agentmodell keine Bilder verarbeiten kann.
          </div>
        </div>
        <div>
          <q-select
            v-model="form.visionModelId"
            outlined
            clearable
            emit-value
            map-options
            options-dense
            label="Visionmodell"
            :display-value="form.visionModelId ? undefined : 'Kein Standardmodell'"
            :options="providers.visionModelOptions"
            :disable="personalBlocked"
            hint="Nur verfügbare Chatmodelle mit Bildeingabe."
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
          />
        </div>
      </q-card-section>

      <q-separator />

      <q-card-section class="default-model-setting">
        <q-avatar rounded color="secondary" text-color="white" icon="compress" />
        <div class="default-model-setting__description">
          <div class="text-subtitle1 text-weight-bold">Kontextkomprimierung</div>
          <div class="text-body2 app-muted">
            Verdichtet ältere Gesprächsinhalte, wenn das Kontextfenster knapp wird.
          </div>
        </div>
        <div>
          <q-select
            v-model="form.compactionModelId"
            outlined
            clearable
            emit-value
            map-options
            options-dense
            label="Komprimierungsmodell"
            :display-value="form.compactionModelId ? undefined : 'Sessionmodell verwenden'"
            :options="providers.selectionOptions(form.compactionModelId)"
            :disable="personalBlocked"
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
          />
        </div>
      </q-card-section>

      <q-separator />

      <q-card-section class="default-model-setting">
        <q-avatar rounded color="accent" text-color="white" icon="title" />
        <div class="default-model-setting__description">
          <div class="text-subtitle1 text-weight-bold">Titelgenerierung</div>
          <div class="text-body2 app-muted">
            Erstellt parallel zum ersten Agent-Run einen kurzen Titel aus der ersten Nachricht und ihren Anhängen.
          </div>
        </div>
        <div>
          <q-select
            v-model="form.titleGenerationModelId"
            outlined
            clearable
            emit-value
            map-options
            options-dense
            label="Modell für Titel"
            :display-value="form.titleGenerationModelId ? undefined : 'Sessionmodell verwenden'"
            :options="providers.selectionOptions(form.titleGenerationModelId)"
            :disable="personalBlocked"
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
          />
          <q-checkbox
            v-model="form.titleVisionFallbackEnabled"
            class="q-mt-sm"
            color="primary"
            label="Fallback auf Vision-Modell bei Bildinhalten"
            :disable="personalBlocked"
          />
        </div>
      </q-card-section>

      <q-separator />

      <q-card-section class="default-model-setting">
        <q-avatar rounded color="deep-purple" text-color="white" icon="bedtime" />
        <div class="default-model-setting__description">
          <div class="text-subtitle1 text-weight-bold">Dreaming</div>
          <div class="text-body2 app-muted">
            Verarbeitet abgeschlossene Gespräche im Hintergrund zu langfristigen Erinnerungen.
          </div>
        </div>
        <div>
          <q-select
            v-model="form.dreamingModelId"
            outlined
            clearable
            emit-value
            map-options
            options-dense
            label="Dreaming-Modell"
            :display-value="form.dreamingModelId ? undefined : 'Agentmodell verwenden'"
            :options="providers.selectionOptions(form.dreamingModelId, true)"
            :disable="personalBlocked"
            hint="Das optionale Dreaming benötigt ein Modell mit Tool-Unterstützung."
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
          />
        </div>
      </q-card-section>

      <template v-for="setting in mediaSettings" :key="setting.field">
        <q-separator />

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
              :display-value="form[setting.field] ? undefined : 'Kein Standardmodell'"
              :options="providers.capabilityOptions(setting.capability)"
              :disable="personalBlocked"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
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
const mediaSettings = [
  {
    field: 'imageGenerationModelId',
    capability: 'image_generation',
    title: 'Bildgenerierung',
    description: 'Erzeugt Bilder aus einer textlichen Beschreibung.',
    label: 'Bildmodell',
    icon: 'image',
    color: 'teal'
  },
  {
    field: 'speechGenerationModelId',
    capability: 'speech_generation',
    title: 'Sprachausgabe',
    description: 'Wandelt Text in gesprochene Sprache um.',
    label: 'Sprachmodell',
    icon: 'record_voice_over',
    color: 'deep-orange'
  },
  {
    field: 'audioGenerationModelId',
    capability: 'audio_generation',
    title: 'Audio und Musik',
    description: 'Erzeugt Klänge, Audio oder Musik aus einer Beschreibung.',
    label: 'Audiomodell',
    icon: 'music_note',
    color: 'indigo'
  },
  {
    field: 'audioTranscriptionModelId',
    capability: 'audio_transcription',
    title: 'Transkription',
    description: 'Wandelt Sprache und andere Audioaufnahmen in Text um.',
    label: 'Transkriptionsmodell',
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
const changed = computed(() => (
  form.defaultModelId !== providers.defaultModelId
  || form.compactionModelId !== providers.compactionModelId
  || form.titleGenerationModelId !== providers.titleGenerationModelId
  || form.titleVisionFallbackEnabled !== providers.titleVisionFallbackEnabled
  || form.dreamingModelId !== providers.dreamingModelId
  || form.visionModelId !== providers.visionModelId
  || form.videoModelId !== providers.videoModelId
  || form.skillCreationModelId !== providers.skillCreationModelId
  || mediaSettings.some((setting) => form[setting.field] !== providers[setting.field])
))
const personalBlocked = computed(() => (
  providers.scope === 'personal' && !providers.personalProvidersEnabled
))

function assignForm() {
  form.defaultModelId = providers.defaultModelId
  form.compactionModelId = providers.compactionModelId
  form.titleGenerationModelId = providers.titleGenerationModelId
  form.titleVisionFallbackEnabled = providers.titleVisionFallbackEnabled
  form.dreamingModelId = providers.dreamingModelId
  form.visionModelId = providers.visionModelId
  form.videoModelId = providers.videoModelId
  form.skillCreationModelId = providers.skillCreationModelId
  for (const setting of mediaSettings) form[setting.field] = providers[setting.field]
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
