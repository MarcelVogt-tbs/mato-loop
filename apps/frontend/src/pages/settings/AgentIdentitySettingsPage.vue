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
        <SettingsScopeToggle
          :model-value="scope"
          :is-admin="auth.isAdmin"
          :disable="identity.settingsLoading || identity.settingsSaving"
          @update:model-value="changeScope"
        />
        <q-btn
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="add"
          label="Persona erstellen"
          :disable="personasBlocked"
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
    <template v-if="auth.isAdmin && scope === 'instance'">
      <q-card flat class="app-surface-tonal q-mb-md">
        <q-card-section class="row items-center justify-between q-gutter-md">
          <div>
            <div class="text-subtitle2 text-weight-bold">Persönliche Anzeigenamen</div>
            <div class="text-caption app-muted">Erlaubt Benutzern, den Instanznamen zu überschreiben.</div>
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
      v-if="scope === 'personal' && (!identity.personalDisplayNamesEnabled || !identity.personalPersonasEnabled)"
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
          {{ scope === 'instance'
            ? 'Instanzweiter Sprechername im Chat.'
            : 'Optionaler persönlicher Sprechername statt des Instanznamens.' }}
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
            :placeholder="scope === 'personal' ? identity.effectiveDisplayName : undefined"
            :rules="scope === 'instance' ? [requiredRule] : []"
            :disable="nameBlocked"
          >
            <template #prepend><q-icon name="badge" /></template>
          </q-input>
          <div class="row justify-end q-gutter-sm q-mt-md">
            <q-btn
              v-if="scope === 'personal'"
              flat
              no-caps
              label="Auf Instanzstandard zurücksetzen"
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

    <q-card flat class="app-surface relative-position">
      <q-card-section class="row items-center justify-between q-gutter-md">
        <div>
          <div class="text-subtitle1 text-weight-bold">Persona-Profile</div>
          <div class="text-caption app-muted">
            Verhalten, Charakter, Ton und allgemeine Arbeitsweise. Eine Persona kann mehreren Agenten dienen.
          </div>
        </div>
        <q-btn
          v-if="scope === 'personal'"
          flat
          no-caps
          label="Instanzstandard verwenden"
          :disable="personasBlocked || identity.defaultPersonaId === null"
          @click="resetDefaultPersona"
        />
      </q-card-section>
      <q-separator />
      <q-list separator>
        <q-item v-for="persona in visiblePersonas" :key="persona.id" class="q-py-md">
          <q-item-section avatar top>
            <q-avatar rounded color="primary" text-color="white" icon="face" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="row items-center q-gutter-sm">
              <span class="text-subtitle1 text-weight-medium">{{ persona.name }}</span>
              <q-badge v-if="persona.id === identity.effectiveDefaultPersonaId" color="primary" label="Standard" />
              <q-badge outline color="grey-7" :label="persona.scope === 'instance' ? 'Instanz' : 'Persönlich'" />
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
                :aria-label="`${persona.name} als Standard verwenden`"
                :disable="personasBlocked || persona.id === identity.defaultPersonaId"
                @click="setDefaultPersona(persona)"
              ><q-tooltip>Als Standard verwenden</q-tooltip></q-btn>
              <q-btn
                v-if="persona.scope === scope"
                flat
                round
                dense
                icon="edit"
                :aria-label="`${persona.name} bearbeiten`"
                :disable="personasBlocked || persona.protected"
                @click="openEditDialog(persona)"
              ><q-tooltip>Bearbeiten</q-tooltip></q-btn>
              <q-btn
                v-if="persona.scope === scope"
                flat
                round
                dense
                color="negative"
                icon="delete_outline"
                :aria-label="`${persona.name} löschen`"
                :disable="personasBlocked || persona.protected || (scope === 'instance' && persona.id === identity.defaultPersonaId)"
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
          </q-card-section>
          <q-separator />
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Abbrechen" :disable="identity.settingsSaving" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="identity.settingsSaving" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'AgentIdentitySettingsPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
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

const scope = computed(() => resolveSettingsScope(route.query.scope, auth.isAdmin))
const settingsReady = computed(() => (
  identity.settingsLoaded && !identity.settingsLoading && identity.scope === scope.value
))
const nameBlocked = computed(() => (
  !settingsReady.value || identity.settingsSaving || (
    scope.value === 'personal' && !identity.personalDisplayNamesEnabled
  )
))
const personasBlocked = computed(() => (
  !settingsReady.value || identity.settingsSaving || (
    scope.value === 'personal' && !identity.personalPersonasEnabled
  )
))
const nameDirty = computed(() => displayName.value.trim() !== savedDisplayName.value)
const visiblePersonas = computed(() => identity.personas.filter((persona) => (
  scope.value === 'personal' || persona.scope === 'instance'
)))
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'

function assignDisplayName() {
  displayName.value = identity.configuredDisplayName || ''
  savedDisplayName.value = displayName.value
}

async function load() {
  try {
    await identity.fetchSettings(scope.value)
    assignDisplayName()
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function changeScope(nextScope) {
  await router.replace({ query: { ...route.query, scope: nextScope } })
}

async function saveDisplayName() {
  if (!await nameFormRef.value?.validate()) return
  const value = displayName.value.trim()
  if (scope.value === 'personal' && !value) {
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

async function resetDisplayName() {
  try {
    await identity.resetDisplayName()
    assignDisplayName()
    $q.notify({ type: 'positive', message: 'Instanzname wird wieder verwendet' })
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
    if (editingPersona.value) await identity.updatePersona(editingPersona.value.id, payload)
    else await identity.createPersona(payload)
    dialogOpen.value = false
    $q.notify({ type: 'positive', message: 'Persona gespeichert' })
  } catch {
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
    $q.notify({ type: 'positive', message: 'Instanzstandard wird wieder verwendet' })
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
    await identity.deletePersona(persona.id)
    $q.notify({ type: 'positive', message: 'Persona gelöscht' })
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

watch(scope, load, { immediate: true })

const confirmDiscardedChanges = async () => (
  !nameDirty.value || await confirm('Ungespeicherte Änderungen verwerfen?')
)

onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
