<template>
  <section>
    <q-tabs
      v-model="tab"
      dense
      no-caps
      align="left"
      active-color="primary"
      indicator-color="primary"
      class="q-mb-lg"
    >
      <q-tab name="profile" icon="person" label="Über mich" />
      <q-tab name="assistant" icon="face" label="Assistent" />
    </q-tabs>

    <q-tab-panels v-model="tab" animated class="bg-transparent">
      <q-tab-panel name="profile" class="q-pa-none">
        <header class="settings-section-header">
          <div>
            <div class="row items-center q-gutter-sm">
              <h2 class="text-h6 text-weight-bold q-my-none">Persönliches Profil</h2>
              <q-badge v-if="dirty" color="warning" text-color="black" label="Ungespeichert" />
            </div>
            <p class="text-body2 app-muted q-mb-none q-mt-xs">
              Persönliche Angaben für passendere Antworten. Benutzername und Kontodaten bleiben unverändert.
            </p>
          </div>
          <q-btn
            color="primary"
            unelevated
            no-caps
            icon="save"
            label="Speichern"
            :loading="profile.saving"
            :disable="profile.loading || !profile.loaded || !dirty"
            @click="save"
          />
        </header>

        <q-banner v-if="profile.loadFailed" rounded class="bg-negative text-white q-mb-md">
          <template #avatar><q-icon name="error_outline" /></template>
          Das persönliche Profil konnte nicht geladen werden.
          <template #action>
            <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
          </template>
        </q-banner>

        <q-banner rounded class="app-surface-tonal q-mb-md">
          <template #avatar><q-icon name="privacy_tip" color="primary" /></template>
          <div class="text-subtitle1 text-weight-bold">Sichtbarkeit bewusst wählen</div>
          <div class="text-body2 app-muted q-mt-xs">
            Angaben für öffentliche Modelle können an externe Modellanbieter übertragen werden.
            „Privat“ bedeutet nicht automatisch lokale Verarbeitung. Speichere hier keine Passwörter,
            Schlüssel oder anderen vertraulichen Daten.
          </div>
        </q-banner>

        <q-card flat class="app-surface">
          <q-card-section>
            <q-btn-toggle
              v-model="profileAudience"
              spread
              no-caps
              unelevated
              toggle-color="primary"
              :options="audienceOptions"
              :disable="profile.loading || profile.saving || !profile.loaded"
            />
          </q-card-section>
          <q-separator />
          <q-card-section>
            <q-form class="q-gutter-y-lg" @submit.prevent="save">
              <div v-for="field in fields" :key="field.name">
                <q-input
                  :model-value="fieldValue(field.name)"
                  outlined
                  clearable
                  maxlength="20000"
                  :label="field.label"
                  :hint="fieldHint(field)"
                  :disable="fieldDisabled(field.name)"
                  @update:model-value="setFieldValue(field.name, $event)"
                />
                <q-checkbox
                  v-if="profileAudience === 'private'"
                  v-model="draft[field.name].shared"
                  class="q-mt-sm"
                  color="primary"
                  label="Auch für öffentliche Modelle verwenden"
                  :disable="profile.loading || profile.saving || !profile.loaded"
                />
              </div>
              <button type="submit" class="hidden" aria-hidden="true" tabindex="-1" />
            </q-form>
          </q-card-section>
        </q-card>

        <q-banner dense rounded inline-actions class="app-surface-tonal q-mt-md">
          <template #avatar><q-icon name="info_outline" color="primary" /></template>
          Weitere persönliche Angaben kannst du als Memory speichern.
          <template #action>
            <q-btn
              flat
              no-caps
              color="primary"
              label="Memory verwalten"
              :to="{ name: 'settings-memory' }"
            />
          </template>
        </q-banner>

        <q-inner-loading :showing="profile.loading" />
      </q-tab-panel>

      <q-tab-panel name="assistant" class="q-pa-none">
        <AgentIdentitySettingsPage />
      </q-tab-panel>
    </q-tab-panels>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import AgentIdentitySettingsPage from 'pages/settings/AgentIdentitySettingsPage.vue'
import { useConfirm } from 'src/composables/confirm'
import {
  profileFactDraft,
  profileFactPayload,
  useUserProfileStore,
  userProfileFields
} from 'stores/user-profile'

defineOptions({ name: 'PersonalizationSettingsPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const profile = useUserProfileStore()
const profileAudience = ref('private')
const draft = reactive(Object.fromEntries(
  userProfileFields.map((field) => [field, profileFactDraft()])
))
const savedDraft = reactive(Object.fromEntries(
  userProfileFields.map((field) => [field, profileFactDraft()])
))

const fields = [
  { name: 'preferredName', label: 'Bevorzugter Name', description: 'Wie dich der Assistent nennen soll.' },
  { name: 'formOfAddress', label: 'Anrede', description: 'Zum Beispiel du, Sie oder eine andere gewünschte Anrede.' },
  { name: 'pronouns', label: 'Pronomen', description: 'Pronomen, die der Assistent für dich verwenden soll.' },
  { name: 'primaryLanguage', label: 'Bevorzugte Sprache', description: 'Die Sprache, in der du normalerweise angesprochen werden möchtest.' },
  { name: 'primaryResidence', label: 'Hauptwohnsitz', description: 'Eine freiwillige Ortsangabe für regional passenden Kontext.' }
]
const audienceOptions = [
  { label: 'Private Modelle', value: 'private', icon: 'lock' },
  { label: 'Öffentliche Modelle', value: 'public', icon: 'public' }
]
const tab = computed({
  get: () => route.query.tab === 'assistant' ? 'assistant' : 'profile',
  set: (value) => router.replace({ query: { ...route.query, tab: value } })
})
const changes = computed(() => {
  const result = {}
  for (const field of userProfileFields) {
    if (
      draft[field].privateValue === savedDraft[field].privateValue &&
      draft[field].publicValue === savedDraft[field].publicValue &&
      draft[field].shared === savedDraft[field].shared
    ) continue
    result[field] = profileFactPayload(draft[field])
  }
  return result
})
const dirty = computed(() => Object.keys(changes.value).length > 0)

function assignDraft(value) {
  for (const field of userProfileFields) {
    const next = profileFactDraft(value[field])
    Object.assign(draft[field], next)
    Object.assign(savedDraft[field], next)
  }
}

function fieldValue(field) {
  return profileAudience.value === 'private' || draft[field].shared
    ? draft[field].privateValue
    : draft[field].publicValue
}

function setFieldValue(field, value) {
  if (profileAudience.value === 'private') draft[field].privateValue = value
  else if (!draft[field].shared) draft[field].publicValue = value
}

function fieldDisabled(field) {
  return profile.loading || profile.saving || !profile.loaded || (
    profileAudience.value === 'public' && draft[field].shared
  )
}

function fieldHint(field) {
  return profileAudience.value === 'public' && draft[field.name].shared
    ? 'Wird aus „Private Modelle“ übernommen.'
    : field.description
}

async function load() {
  try {
    assignDraft(await profile.fetchProfile())
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function save() {
  if (!profile.loaded || !dirty.value) return
  try {
    assignDraft(await profile.saveProfile(changes.value))
    $q.notify({ type: 'positive', message: 'Persönliches Profil gespeichert' })
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function confirmDiscardedChanges() {
  if (profile.saving) return false
  if (!dirty.value) return true
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

onMounted(load)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
