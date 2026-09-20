<template>
  <router-view v-if="route.name === 'settings-mcp-tools'" />
  <section v-else>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">MCP-Server</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">Externe Tools über das Model Context Protocol bereitstellen.</p>
      </div>
      <div class="settings-scope-actions row items-center col-auto">
        <SettingsScopeToggle :model-value="servers.scope" :is-admin="auth.isAdmin" :disable="servers.loading || removingId !== null" @update:model-value="changeScope" />
        <q-btn color="primary" unelevated no-caps no-wrap icon="add" label="MCP-Server hinzufügen" :disable="servers.loading || personalUnavailable" @click="openCreate" />
      </div>
    </header>

    <q-banner rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="security" color="primary" /></template>
      MCP-Tools können externe Aktionen ausführen. Prüfe Server und Tools; Rechte werden in Agentenprofilen festgelegt.
    </q-banner>
    <q-card v-if="auth.isAdmin" flat class="app-surface-tonal q-mb-md">
      <q-card-section class="row items-center justify-between q-gutter-md">
        <div>
          <div class="text-subtitle2 text-weight-bold">Persönliche MCP-Server</div>
          <div class="text-caption app-muted">Erlaubt Benutzern, eigene MCP-Server einzurichten.</div>
        </div>
        <div class="row items-center q-gutter-sm">
          <q-toggle v-model="personalAllowed" color="primary" label="Erlaubt" :disable="servers.settingsLoading || servers.settingsSaving || servers.settingsLoadFailed" />
          <q-btn unelevated no-caps color="primary" icon="save" label="Speichern" :loading="servers.settingsSaving" :disable="servers.settingsLoading || !settingsDirty" @click="saveSettings" />
        </div>
      </q-card-section>
    </q-card>
    <q-banner v-if="personalBlocked" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="block" color="warning" /></template>
      Persönliche MCP-Server sind für diese Instanz deaktiviert.
    </q-banner>
    <q-banner v-if="servers.settingsLoadFailed" rounded class="app-surface-tonal q-mb-md">
      Die MCP-Einstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="primary" label="Erneut laden" @click="loadPage" /></template>
    </q-banner>
    <q-banner v-if="servers.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die MCP-Server konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="primary" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <q-card v-if="!personalUnavailable" flat class="app-surface">
      <q-list separator>
        <q-item v-if="servers.loaded && !servers.servers.length" class="q-py-lg">
          <q-item-section avatar><q-avatar color="grey-7" text-color="white" icon="hub" /></q-item-section>
          <q-item-section>
            <q-item-label>Noch kein MCP-Server konfiguriert</q-item-label>
            <q-item-label caption>Füge einen streamfähigen HTTP-Endpunkt hinzu.</q-item-label>
          </q-item-section>
        </q-item>
        <q-item v-for="server in servers.servers" :key="server.id" class="q-py-md">
          <q-item-section avatar top><q-avatar color="primary" text-color="white" icon="hub" /></q-item-section>
          <q-item-section>
            <div class="row items-center q-gutter-sm">
              <q-item-label class="text-weight-medium">{{ server.name }}</q-item-label>
              <q-chip dense :color="server.enabled ? 'positive' : 'grey-7'" text-color="white">{{ server.enabled ? 'Aktiv' : 'Deaktiviert' }}</q-chip>
              <q-chip v-if="server.status && server.status !== 'ready'" dense outline color="warning">{{ statusLabel(server.status) }}</q-chip>
            </div>
            <q-item-label caption lines="1">{{ server.endpoint }}</q-item-label>
            <q-item-label caption>{{ server.tools?.length || server.toolCount || 0 }} erkannte Tools</q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row items-center no-wrap q-gutter-xs">
              <q-btn flat dense :round="$q.screen.lt.md" no-caps icon="construction" :label="$q.screen.gt.sm ? 'Tools' : undefined" aria-label="Tools anzeigen" :to="{ name: 'settings-mcp-tools', params: { serverId: server.id }, query: { scope: servers.scope } }" />
              <template v-if="$q.screen.lt.md">
                <q-btn flat round dense icon="more_vert" :aria-label="`Aktionen für ${server.name}`" :loading="refreshingId === server.id">
                  <q-menu auto-close>
                    <q-list>
                      <q-item clickable :disable="refreshingId !== null" @click="refreshServer(server)">
                        <q-item-section avatar><q-icon name="refresh" /></q-item-section>
                        <q-item-section>Tools aktualisieren</q-item-section>
                      </q-item>
                      <q-item clickable @click="openEdit(server)">
                        <q-item-section avatar><q-icon name="edit" /></q-item-section>
                        <q-item-section>Bearbeiten</q-item-section>
                      </q-item>
                      <q-item clickable :disable="removingId !== null" @click="removeServer(server)">
                        <q-item-section avatar><q-icon name="delete" color="negative" /></q-item-section>
                        <q-item-section class="text-negative">Löschen</q-item-section>
                      </q-item>
                    </q-list>
                  </q-menu>
                </q-btn>
              </template>
              <template v-else>
                <q-btn flat dense no-caps icon="refresh" label="Tools aktualisieren" aria-label="Tools aktualisieren" :loading="refreshingId === server.id" @click="refreshServer(server)" />
                <q-btn flat dense no-caps icon="edit" label="Bearbeiten" :aria-label="`${server.name} bearbeiten`" @click="openEdit(server)" />
                <q-btn flat round dense color="negative" icon="delete" :aria-label="`${server.name} löschen`" :loading="removingId === server.id" :disable="removingId !== null" @click="removeServer(server)" />
              </template>
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="servers.loading" />
    </q-card>
    <McpServerDialog v-if="!personalUnavailable" v-model="dialogOpen" :server="selectedServer" />
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import McpServerDialog from 'components/McpServerDialog.vue'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { useAuthStore } from 'stores/auth'
import { useMcpServerStore } from 'stores/mcp-servers'

defineOptions({ name: 'McpSettingsPage' })
const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const servers = useMcpServerStore()
const auth = useAuthStore()
const dialogOpen = ref(false)
const selectedServer = ref(null)
const personalAllowed = ref(false)
const refreshingId = ref(null)
const removingId = ref(null)
const personalBlocked = computed(() => servers.scope === 'personal' && servers.settingsLoaded && !servers.personalAllowed)
const personalUnavailable = computed(() => servers.scope === 'personal' && (
  !servers.settingsLoaded || servers.settingsLoadFailed || !servers.personalAllowed
))
const settingsDirty = computed(() => servers.settingsLoaded && personalAllowed.value !== servers.personalAllowed)

function openCreate() { selectedServer.value = null; dialogOpen.value = true }
function openEdit(server) { selectedServer.value = server; dialogOpen.value = true }
function statusLabel(status) {
  return { unavailable: 'Nicht erreichbar', error: 'Fehler', stale: 'Tools veraltet' }[status] || status
}
async function load() { try { await servers.fetchServers() } catch { /* Central API toast. */ } }
async function loadPage() {
  try {
    const settings = await servers.fetchSettings()
    personalAllowed.value = settings.personalAllowed
  } catch { return }
  if (personalBlocked.value) {
    servers.servers = []
    servers.loaded = true
    return
  }
  await load()
}
async function saveSettings() {
  try {
    const settings = await servers.saveSettings(personalAllowed.value)
    personalAllowed.value = settings.personalAllowed
    if (personalBlocked.value) {
      dialogOpen.value = false
      servers.servers = []
      servers.loaded = true
    } else if (servers.scope === 'personal') await load()
    $q.notify({ type: 'positive', message: 'MCP-Einstellungen gespeichert' })
  } catch { /* Central API toast. */ }
}
async function changeScope(scope) {
  dialogOpen.value = false
  servers.selectScope(scope)
  await loadPage()
}
async function refreshServer(server) {
  refreshingId.value = server.id
  try {
    await servers.refreshServer(server.id)
    $q.notify({ type: 'positive', message: 'MCP-Tools aktualisiert' })
  } finally { refreshingId.value = null }
}
async function removeServer(server) {
  if (!await confirm(`MCP-Server „${server.name}“ vollständig löschen?`, 'MCP-Server löschen', { okLabel: 'Löschen' })) return
  removingId.value = server.id
  try {
    await servers.removeServer(server.id)
    $q.notify({ type: 'positive', message: 'MCP-Server gelöscht' })
  } finally { removingId.value = null }
}
onMounted(() => {
  servers.selectScope(resolveSettingsScope(route.query.scope, auth.isAdmin))
  if (route.name !== 'settings-mcp-tools') void loadPage()
})
</script>
