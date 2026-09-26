<template>
  <router-view v-if="showEditor" />
  <section v-else>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Agenten</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Wiederverwendbare Agentenprofile mit Anweisungen und Berechtigungen verwalten.
        </p>
      </div>
      <div class="settings-scope-actions row items-center col-auto">
        <q-btn
          v-if="auth.isAdmin || !personalBlocked"
          class="col-auto"
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="add"
          label="Neues Profil erstellen"
          :disable="agents.loading"
          :to="{ name: 'settings-agent-create' }"
        />
      </div>
    </header>

    <q-card v-if="auth.isAdmin" flat class="app-surface-tonal q-mb-md">
      <q-card-section class="row items-center justify-between q-gutter-md">
        <div>
          <div class="text-subtitle2 text-weight-bold">Persönliche Agentenprofile</div>
          <div class="text-caption app-muted">Erlaubt Benutzern, eigene Profile zu erstellen und zu verwenden.</div>
        </div>
        <q-toggle
          :model-value="agents.personalAgentsEnabled"
          label="Erlaubt"
          :disable="gateSaving"
          @update:model-value="setPersonalAgentsEnabled"
        />
      </q-card-section>
    </q-card>

    <q-banner v-if="personalBlocked" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="person" color="primary" /></template>
      Persönliche Agenten wurden für diese Instanz deaktiviert.
    </q-banner>

    <q-banner v-if="agents.loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Agentenprofile konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <q-card flat class="app-surface relative-position">
      <q-card-section v-if="agents.loaded && !visibleAgents.length" class="q-pa-xl">
        <AppStateMessage
          icon="smart_toy"
          title="Noch keine Agentenprofile"
          description="Erstelle ein Profil mit eigenen Anweisungen und Berechtigungen."
        />
      </q-card-section>
      <q-list v-else separator>
        <q-item v-for="agent in visibleAgents" :key="agent.id" class="q-py-md">
          <q-item-section avatar top>
            <q-avatar rounded color="primary" text-color="white" icon="smart_toy" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="text-subtitle1 text-weight-medium">
              {{ agent.name }} <q-badge outline color="grey-7" :label="agent.scope === 'instance' ? 'Global' : 'Persönlich'" />
              <q-badge v-if="agent.enabled === false" outline color="grey-7" label="Deaktiviert" />
              <q-badge v-if="agent.effective === false" outline color="warning" label="Nicht verfügbar" />
            </q-item-label>
            <q-item-label caption class="text-body2 q-mt-xs">
              {{ agent.description || 'Keine Beschreibung' }}
            </q-item-label>
            <q-item-label caption class="q-mt-xs">
              Persona: {{ agent.resolvedPersonaName || 'Standard' }}
            </q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row no-wrap q-gutter-xs">
              <q-toggle
                dense
                :model-value="agent.enabled !== false"
                :aria-label="`${agent.name} ${agent.enabled === false ? 'aktivieren' : 'deaktivieren'}`"
                :disable="agent.protected || switchingId === agent.id || agents.saving || (agent.scope === 'instance' ? !auth.isAdmin : personalBlocked)"
                @update:model-value="setAgentEnabled(agent, $event)"
              />
              <q-btn
                flat
                round
                dense
                :icon="agent.scope === 'instance' && !auth.isAdmin ? 'visibility' : 'edit'"
                :aria-label="`${agent.name} ${agent.scope === 'instance' && !auth.isAdmin ? 'ansehen' : 'bearbeiten'}`"
                :disable="agent.scope === 'personal' && personalBlocked"
                :to="{ name: 'settings-agent-edit', params: { agentId: agent.id }, query: { scope: agent.scope } }"
              ><q-tooltip>{{ agent.scope === 'instance' && !auth.isAdmin ? 'Ansehen' : 'Bearbeiten' }}</q-tooltip></q-btn>
              <q-btn
                flat
                round
                dense
                color="negative"
                icon="delete_outline"
                :aria-label="`${agent.name} löschen`"
                v-if="agent.scope === 'personal' || auth.isAdmin"
                :disable="(agent.scope === 'personal' && personalBlocked) || agent.protected"
                @click="removeAgent(agent)"
              ><q-tooltip>Löschen</q-tooltip></q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="agents.loading" />
    </q-card>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import { api } from 'src/boot/axios'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import { useConfirm } from 'src/composables/confirm'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'AgentProfileSettingsPage' })

const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const agents = useAgentProfileStore()
const auth = useAuthStore()
const showEditor = computed(() => ['settings-agent-create', 'settings-agent-edit'].includes(route.name))
const storedAgents = ref([])
const switchingId = ref(null)
const gateSaving = ref(false)
const visibleAgents = computed(() => [
  ...agents.manageableAgents.filter((agent) => agent.scope !== 'integration'),
  ...storedAgents.value
])
const personalBlocked = computed(() => !agents.personalAgentsEnabled)

async function load() {
  try {
    const result = await agents.fetchAgents()
    storedAgents.value = result.personalAgentsEnabled === false
      ? (await api.get('/agent-profiles', { params: { scope: 'personal' } })).data.agents || []
      : []
  } catch {
    agents.loadFailed = true
  }
}

async function removeAgent(agent) {
  const accepted = await confirm(
    `Soll der Agent „${agent.name}“ dauerhaft gelöscht werden? Verwendete Sessions wechseln zu „Allgemein“.`,
    'Agent löschen',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  try {
    agents.selectScope(agent.scope)
    await agents.deleteAgent(agent.id)
    await load()
    $q.notify({ type: 'positive', message: 'Agent gelöscht' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function setPersonalAgentsEnabled(enabled) {
  gateSaving.value = true
  try {
    await agents.setPersonalAgentsEnabled(enabled)
    await load()
    $q.notify({ type: 'positive', message: 'Einstellung gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    gateSaving.value = false
  }
}

async function setAgentEnabled(agent, enabled) {
  switchingId.value = agent.id
  try {
    await agents.setAgentEnabled(agent.id, enabled, agent.scope)
    await load()
    $q.notify({ type: 'positive', message: enabled ? 'Agent aktiviert' : 'Agent deaktiviert' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    switchingId.value = null
  }
}

onMounted(() => {
  if (!showEditor.value) void load()
})
watch(showEditor, (editing) => {
  if (!editing) void load()
})
</script>
