<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Personas</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Wiederverwendbare Verhaltensprofile für Agenten verwalten.
        </p>
      </div>
      <div class="settings-scope-actions row items-center col-auto">
        <q-btn
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="add"
          label="Persona erstellen"
          :disable="personasBlocked && !auth.isAdmin"
          @click="openCreateDialog"
        />
      </div>
    </header>

    <q-banner v-if="identity.settingsLoadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Personalisierung konnte nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <div v-if="identity.settingsLoading" class="row justify-center q-pa-xl">
      <q-spinner color="primary" size="2.5rem" />
    </div>

    <template v-if="settingsReady">
    <template v-if="auth.isAdmin">
      <q-card flat class="app-surface-tonal q-mb-md">
        <q-card-section class="row items-center justify-between q-gutter-md">
          <div>
            <div class="text-subtitle2 text-weight-bold">Persönliche Anzeigenamen</div>
            <div class="text-caption app-muted">Erlaubt Benutzern, den globalen Anzeigenamen zu überschreiben.</div>
          </div>
          <q-toggle
            :model-value="identity.personalDisplayNamesEnabled"
            color="primary"
            label="Erlaubt"
            :disable="!settingsReady || identity.settingsSaving"
            @update:model-value="setPersonalDisplayNamesEnabled"
          />
        </q-card-section>
      </q-card>
      <q-card flat class="app-surface-tonal q-mb-md">
        <q-card-section class="row items-center justify-between q-gutter-md">
          <div>
            <div class="text-subtitle2 text-weight-bold">Persönliche Personas</div>
            <div class="text-caption app-muted">Erlaubt eigene Personas für persönliche Agenten.</div>
          </div>
          <q-toggle
            :model-value="identity.personalPersonasEnabled"
            color="primary"
            label="Erlaubt"
            :disable="!settingsReady || identity.settingsSaving"
            @update:model-value="setPersonalPersonasEnabled"
          />
        </q-card-section>
      </q-card>
    </template>

    <q-banner
      v-if="!identity.personalDisplayNamesEnabled || !identity.personalPersonasEnabled"
      rounded
      class="app-surface-tonal q-mb-md"
    >
      <template #avatar><q-icon name="admin_panel_settings" color="primary" /></template>
      Deaktivierte persönliche Werte bleiben gespeichert, werden aber nicht verwendet oder bearbeitet.
    </q-banner>

    <q-card flat class="app-surface q-mb-md">
      <q-card-section>
        <div class="text-subtitle1 text-weight-bold">Anzeigename</div>
        <div class="text-caption app-muted">
          Optionaler persönlicher Sprechername statt des globalen Anzeigenamens.
        </div>
      </q-card-section>
      <q-separator />
      <q-card-section>
        <q-form ref="nameFormRef" @submit.prevent="saveDisplayName">
          <q-input
            v-model="displayName"
            outlined
            label="Agent-Name"
            maxlength="80"
            counter
            :placeholder="identity.effectiveDisplayName"
            :disable="nameBlocked"
          >
            <template #prepend><q-icon name="badge" /></template>
          </q-input>
          <div class="row justify-end q-gutter-sm q-mt-md">
            <q-btn
              flat
              no-caps
              label="Auf globalen Standard zurücksetzen"
              :disable="nameBlocked || !identity.configuredDisplayName"
              @click="resetDisplayName"
            />
            <q-btn
              unelevated
              no-caps
              color="primary"
              icon="save"
              label="Speichern"
              type="submit"
              :loading="identity.settingsSaving"
              :disable="nameBlocked || !nameDirty"
            />
          </div>
        </q-form>
      </q-card-section>
    </q-card>

    <q-card v-if="auth.isAdmin && instanceIdentity" flat class="app-surface q-mb-md">
      <q-card-section class="text-subtitle1 text-weight-bold">Globaler Anzeigename</q-card-section>
      <q-card-section>
        <q-input v-model="instanceDisplayName" outlined label="Agent-Name" maxlength="80" />
        <div class="row justify-end q-mt-md">
          <q-btn unelevated no-caps color="primary" label="Speichern" :disable="!instanceDisplayName.trim() || instanceDisplayName === instanceIdentity?.configuredDisplayName" @click="saveInstanceDisplayName" />
        </div>
      </q-card-section>
    </q-card>

    <q-card flat class="app-surface relative-position">
      <q-card-section class="row items-center justify-between q-gutter-md">
        <div>
          <div class="text-subtitle1 text-weight-bold">Persona-Profile</div>
          <div class="text-caption app-muted">
            Verhalten, Charakter, Ton und allgemeine Arbeitsweise. Eine Persona kann mehreren Agenten dienen.
          </div>
        </div>
        <q-btn
          flat
          no-caps
          label="Globalen Standard verwenden"
          :disable="personasBlocked || identity.defaultPersonaId === null"
          @click="resetDefaultPersona"
        />
      </q-card-section>
      <q-card-section v-if="auth.isAdmin && instanceIdentity" class="q-pt-none">
        <q-select :model-value="instanceIdentity?.defaultPersonaId" outlined emit-value map-options label="Globale Standard-Persona" :options="instancePersonas.map((persona) => ({ label: persona.name, value: persona.id }))" @update:model-value="setInstanceDefaultPersona" />
      </q-card-section>
      <q-separator />
      <q-list separator>
        <q-item v-for="persona in identity.personas" :key="persona.id" class="q-py-md">
          <q-item-section avatar top>
            <q-avatar rounded color="primary" text-color="white" icon="face" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="row items-center q-gutter-sm">
              <span class="text-subtitle1 text-weight-medium">{{ persona.name }}</span>
              <q-badge v-if="persona.id === identity.effectiveDefaultPersonaId" color="primary" label="Standard" />
              <q-badge outline color="grey-7" :label="persona.scope === 'instance' ? 'Global' : 'Persönlich'" />
              <q-badge v-if="persona.effective === false" outline color="warning" label="Deaktiviert" />
            </q-item-label>
            <q-item-label caption lines="2" class="text-body2 q-mt-xs">
              {{ persona.instructions }}
            </q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row no-wrap q-gutter-xs">
              <q-btn
                flat
                round
                dense
                icon="star_outline"
                :color="persona.id === identity.effectiveDefaultPersonaId ? 'primary' : undefined"
                :aria-label="`${persona.name} als meinen Standard verwenden`"
                :disable="personasBlocked || persona.id === identity.defaultPersonaId"
                @click="setDefaultPersona(persona)"
              ><q-tooltip>Als meinen Standard verwenden</q-tooltip></q-btn>
              <q-btn
                v-if="persona.scope === 'personal' || auth.isAdmin"
                flat
                round
                dense
                icon="edit"
                :aria-label="`${persona.name} bearbeiten`"
                :disable="(persona.scope === 'personal' && personasBlocked) || persona.protected"
                @click="openEditDialog(persona)"
              ><q-tooltip>Bearbeiten</q-tooltip></q-btn>
              <q-btn
                v-if="persona.scope === 'personal' || auth.isAdmin"
                flat
                round
                dense
                color="negative"
                icon="delete_outline"
                :aria-label="`${persona.name} löschen`"
                :disable="(persona.scope === 'personal' && personasBlocked) || persona.protected || (persona.scope === 'instance' && persona.id === instanceIdentity?.defaultPersonaId)"
                @click="removePersona(persona)"
              ><q-tooltip>Löschen</q-tooltip></q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>
    </q-card>
    </template>

    <q-dialog
      v-model="dialogOpen"
      aria-labelledby="persona-dialog-title"
      :maximized="$q.screen.lt.sm"
      :persistent="identity.settingsSaving"
    >
      <q-card class="app-surface column no-wrap full-width">
        <q-form ref="personaFormRef" class="column no-wrap full-height" @submit.prevent="savePersona">
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar color="primary" text-color="white" :icon="editingPersona ? 'edit' : 'add_reaction'" />
            <div class="col">
              <div id="persona-dialog-title" class="text-h6">{{ editingPersona ? 'Persona bearbeiten' : 'Persona erstellen' }}</div>
              <div class="text-body2 app-muted">Verhalten und Charakter unabhängig von einer Aufgabe festlegen.</div>
            </div>
            <q-btn flat round dense icon="close" aria-label="Dialog schließen" :disable="identity.settingsSaving" v-close-popup />
          </q-card-section>
          <q-separator />
          <q-card-section class="col scroll">
            <q-input
              v-model="personaForm.name"
              outlined
              autofocus
              label="Name"
              maxlength="80"
              counter
              lazy-rules
              :rules="[requiredRule]"
            />
            <q-input
              v-model="personaForm.instructions"
              class="q-mt-md"
              outlined
              type="textarea"
              rows="12"
              label="Verhalten und Charakter"
              maxlength="16000"
              counter
              lazy-rules
              :rules="[requiredRule]"
              hint="Beschreibe Ton, Persönlichkeit, Rolle und allgemeine Arbeitsweise."
            />
            <q-banner rounded class="app-surface-tonal q-mt-md">
              <template #avatar><q-icon name="privacy_tip" color="primary" /></template>
              Die Persona wird an das ausgewählte Modell übertragen. Speichere hier keine Secrets.
            </q-banner>
            <q-checkbox v-if="auth.isAdmin && !editingPersona" v-model="shared" label="Global" class="q-mt-md" />
          </q-card-section>
          <q-separator />
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Abbrechen" :disable="identity.settingsSaving" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="identity.settingsSaving" :disable="personasBlocked && !(auth.isAdmin && (shared || editingPersona?.scope === 'instance'))" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { api } from 'src/boot/axios'
import { useConfirm } from 'src/composables/confirm'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'AgentIdentitySettingsPage' })

const $q = useQuasar()
const { confirm } = useConfirm()
const identity = useAgentIdentityStore()
const auth = useAuthStore()
const nameFormRef = ref(null)
const personaFormRef = ref(null)
const displayName = ref('')
const savedDisplayName = ref('')
const dialogOpen = ref(false)
const editingPersona = ref(null)
const personaForm = reactive({ name: '', instructions: '' })
const shared = ref(false)
const instanceIdentity = ref(null)
const instanceDisplayName = ref('')

const settingsReady = computed(() => (
  identity.settingsLoaded && !identity.settingsLoading && identity.scope === 'personal'
))
const nameBlocked = computed(() => (
  !settingsReady.value || identity.settingsSaving || !identity.personalDisplayNamesEnabled
))
const personasBlocked = computed(() => (
  !settingsReady.value || identity.settingsSaving || !identity.personalPersonasEnabled
))
const nameDirty = computed(() => displayName.value.trim() !== savedDisplayName.value)
const instancePersonas = computed(() => identity.personas.filter((persona) => persona.scope === 'instance'))
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'

function assignDisplayName() {
  displayName.value = identity.configuredDisplayName || ''
  savedDisplayName.value = displayName.value
}

async function load() {
  const personalDraft = nameDirty.value ? displayName.value : null
  const sharedDraft = instanceIdentity.value && instanceDisplayName.value !== instanceIdentity.value.configuredDisplayName
    ? instanceDisplayName.value : null
  try {
    await identity.fetchSettings('personal')
    assignDisplayName()
    if (personalDraft !== null) displayName.value = personalDraft
    if (auth.isAdmin) {
      instanceIdentity.value = (await api.get('/agent-identity/settings', { params: { scope: 'instance' } })).data
      instanceDisplayName.value = instanceIdentity.value.configuredDisplayName || ''
      if (sharedDraft !== null) instanceDisplayName.value = sharedDraft
    }
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function saveDisplayName() {
  if (!await nameFormRef.value?.validate()) return
  const value = displayName.value.trim()
  if (!value) {
    await resetDisplayName()
    return
  }
  try {
    await identity.saveDisplayName(value)
    assignDisplayName()
    $q.notify({ type: 'positive', message: 'Anzeigename gespeichert' })
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function saveInstanceDisplayName() {
  if (!instanceDisplayName.value.trim()) return
  try {
    instanceIdentity.value = (await api.patch('/agent-identity/settings/display-name', { displayName: instanceDisplayName.value.trim() }, { params: { scope: 'instance' } })).data
    await load()
    $q.notify({ type: 'positive', message: 'Globaler Anzeigename gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function setInstanceDefaultPersona(personaId) {
  try {
    await api.patch('/agent-identity/personas/default', { personaId }, { params: { scope: 'instance' } })
    await load()
    $q.notify({ type: 'positive', message: 'Globale Standard-Persona gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function resetDisplayName() {
  try {
    await identity.resetDisplayName()
    assignDisplayName()
    $q.notify({ type: 'positive', message: 'Globaler Anzeigename wird wieder verwendet' })
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function setPersonalDisplayNamesEnabled(enabled) {
  try {
    await identity.setPersonalDisplayNamesEnabled(enabled)
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function setPersonalPersonasEnabled(enabled) {
  try {
    await identity.setPersonalPersonasEnabled(enabled)
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

function openCreateDialog() {
  editingPersona.value = null
  shared.value = false
  Object.assign(personaForm, { name: '', instructions: '' })
  dialogOpen.value = true
}

function openEditDialog(persona) {
  editingPersona.value = persona
  Object.assign(personaForm, { name: persona.name, instructions: persona.instructions })
  dialogOpen.value = true
}

async function savePersona() {
  if (!await personaFormRef.value?.validate()) return
  const payload = {
    name: personaForm.name.trim(),
    instructions: personaForm.instructions.trim()
  }
  try {
    await identity.fetchSettings(editingPersona.value?.scope || (shared.value ? 'instance' : 'personal'))
    if (editingPersona.value) await identity.updatePersona(editingPersona.value.id, payload)
    else await identity.createPersona(payload)
    await load()
    dialogOpen.value = false
    $q.notify({ type: 'positive', message: 'Persona gespeichert' })
  } catch {
    await load()
    // The central Axios interceptor displays the error message.
  }
}

async function setDefaultPersona(persona) {
  try {
    await identity.setDefaultPersona(persona.id)
    $q.notify({ type: 'positive', message: 'Standard-Persona geändert' })
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function resetDefaultPersona() {
  try {
    await identity.setDefaultPersona(null)
    $q.notify({ type: 'positive', message: 'Globaler Standard wird wieder verwendet' })
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function removePersona(persona) {
  const accepted = await confirm(
    `Persona „${persona.name}“ löschen? Betroffene Agenten verwenden danach die Standard-Persona.`,
    'Persona löschen?',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  try {
    await identity.fetchSettings(persona.scope)
    await identity.deletePersona(persona.id)
    await load()
    $q.notify({ type: 'positive', message: 'Persona gelöscht' })
  } catch {
    await load()
    // The central Axios interceptor displays the error message.
  }
}

void load()

const confirmDiscardedChanges = async () => (
  (!nameDirty.value && (!auth.isAdmin || instanceDisplayName.value === instanceIdentity.value?.configuredDisplayName)) || await confirm('Ungespeicherte Änderungen verwerfen?')
)

onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
