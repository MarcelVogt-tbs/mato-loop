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
        <q-toggle
          class="col-auto"
          v-if="auth.isAdmin && agents.scope === 'instance'"
          :model-value="agents.personalAgentsEnabled"
          label="Persönliche Agenten"
          @update:model-value="setPersonalAgentsEnabled"
        />
        <SettingsScopeToggle
          :model-value="agents.scope"
          :is-admin="auth.isAdmin"
          :disable="agents.loading || agents.saving"
          @update:model-value="changeScope"
        />
        <q-btn
          class="col-auto"
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="add"
          label="Agentenprofil erstellen"
          :disable="personalBlocked || agents.loading"
          :to="{ name: 'settings-agent-create', query: { scope: agents.scope } }"
        />
      </div>
    </header>

    <q-banner v-if="agents.scope === 'personal'" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="person" color="primary" /></template>
      {{ personalBlocked
        ? 'Persönliche Agenten wurden für diese Instanz deaktiviert.'
        : 'Persönliche Agenten stehen nur dir zur Verfügung. Instanz-Agenten bleiben zusätzlich auswählbar.' }}
    </q-banner>

    <q-banner v-if="agents.loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Agentenprofile konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <q-card flat class="app-surface relative-position">
      <q-card-section v-if="agents.loaded && !agents.scopedAgents.length" class="text-center q-pa-xl">
        <q-icon name="smart_toy" size="48px" color="grey-6" />
        <div class="text-subtitle1 text-weight-medium q-mt-sm">Noch keine Agentenprofile</div>
        <div class="text-body2 app-muted q-mt-xs">
          Erstelle ein Profil mit eigenen Anweisungen und Berechtigungen.
        </div>
      </q-card-section>
      <q-list v-else separator>
        <q-item v-for="agent in agents.scopedAgents" :key="agent.id" class="q-py-md">
          <q-item-section avatar top>
            <q-avatar rounded color="primary" text-color="white" icon="smart_toy" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="text-subtitle1 text-weight-medium">{{ agent.name }}</q-item-label>
            <q-item-label caption class="text-body2 q-mt-xs">
              {{ agent.description || 'Keine Beschreibung' }}
            </q-item-label>
            <q-item-label caption class="q-mt-xs">
              Gemeinsame Persona {{ agent.personaMode === 'replace' ? 'weglassen' : 'verwenden' }}
            </q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row no-wrap q-gutter-xs">
              <q-btn
                flat
                round
                dense
                icon="edit"
                :aria-label="`${agent.name} bearbeiten`"
                :disable="personalBlocked"
                :to="{ name: 'settings-agent-edit', params: { agentId: agent.id }, query: { scope: agents.scope } }"
              ><q-tooltip>Bearbeiten</q-tooltip></q-btn>
              <q-btn
                flat
                round
                dense
                color="negative"
                icon="delete_outline"
                :aria-label="`${agent.name} löschen`"
                :disable="personalBlocked || agent.id === agents.defaultAgentProfileId"
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
import { computed, onMounted } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'AgentProfileSettingsPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const agents = useAgentProfileStore()
const auth = useAuthStore()
const showEditor = computed(() => ['settings-agent-create', 'settings-agent-edit'].includes(route.name))
const personalBlocked = computed(() => agents.scope === 'personal' && !agents.personalAgentsEnabled)

async function load() {
  await agents.fetchAgents(agents.scope)
}

async function changeScope(scope) {
  agents.selectScope(scope)
  await router.replace({ query: { ...route.query, scope: agents.scope } })
  await load()
}

async function removeAgent(agent) {
  const accepted = await confirm(
    `Soll der Agent „${agent.name}“ dauerhaft gelöscht werden?`,
    'Agent löschen',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  try {
    await agents.deleteAgent(agent.id)
    $q.notify({ type: 'positive', message: 'Agent gelöscht' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function setPersonalAgentsEnabled(enabled) {
  try {
    await agents.setPersonalAgentsEnabled(enabled)
    $q.notify({ type: 'positive', message: 'Einstellung gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

onMounted(() => {
  agents.selectScope(resolveSettingsScope(route.query.scope, auth.isAdmin))
  if (!showEditor.value) void load()
})
</script>
