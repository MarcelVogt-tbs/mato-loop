<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Standardmodelle</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Modelle für Agent-Läufe und automatische Hintergrundaufgaben festlegen.
        </p>
      </div>
      <q-btn
        color="primary"
        unelevated
        no-caps
        icon="save"
        label="Speichern"
        :loading="saving"
        :disable="!loaded || providers.loading || !changed"
        @click="save"
      />
    </header>

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

    <q-card flat class="app-surface default-models-card">
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
            :options="providers.selectionOptions(form.defaultModelId)"
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
            Erstellt parallel zum ersten Agent-Run einen kurzen Titel aus der ersten Nutzernachricht.
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
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
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
            hint="Dreaming benötigt ein Modell mit Tool-Unterstützung."
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
import { useProviderStore } from 'stores/providers'

defineOptions({ name: 'DefaultModelSettingsPage' })

const $q = useQuasar()
const providers = useProviderStore()
const loaded = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
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
  dreamingModelId: null,
  imageGenerationModelId: null,
  speechGenerationModelId: null,
  audioGenerationModelId: null,
  audioTranscriptionModelId: null
})
const changed = computed(() => (
  form.defaultModelId !== providers.defaultModelId
  || form.compactionModelId !== providers.compactionModelId
  || form.titleGenerationModelId !== providers.titleGenerationModelId
  || form.dreamingModelId !== providers.dreamingModelId
  || mediaSettings.some((setting) => form[setting.field] !== providers[setting.field])
))

function assignForm() {
  form.defaultModelId = providers.defaultModelId
  form.compactionModelId = providers.compactionModelId
  form.titleGenerationModelId = providers.titleGenerationModelId
  form.dreamingModelId = providers.dreamingModelId
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

onMounted(load)
</script>
