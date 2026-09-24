<template>
  <section>
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
      <q-btn flat round dense icon="arrow_back" aria-label="Zurück zu den MCP-Servern" :to="{ name: 'settings-mcp', query: { scope } }" />
      <q-avatar color="primary" text-color="white" icon="construction" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="text-h6 text-weight-bold">{{ server ? `Tools von ${server.name}` : 'MCP-Tools' }}</div>
        <div class="text-body2 app-muted">Erkannte Tools anzeigen. Rechte werden in Agentenprofilen festgelegt.</div>
      </q-toolbar-title>
    </q-toolbar>

    <q-banner rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="info_outline" color="primary" /></template>
      Tool-Namen, Beschreibungen und Hinweise stammen vom MCP-Server und sind nicht verifiziert.
    </q-banner>
    <q-banner v-if="!server && !servers.loading" rounded class="app-surface-tonal">Der MCP-Server wurde nicht gefunden.</q-banner>
    <q-banner v-else-if="server && !server.tools?.length" rounded class="app-surface-tonal">Dieser Server stellt derzeit keine Tools bereit.</q-banner>

    <q-card v-for="tool in server?.tools || []" :key="tool.name" flat class="app-surface q-mb-md">
      <q-card-section>
        <div class="row items-center q-gutter-sm">
          <div class="text-subtitle1 text-weight-bold">{{ tool.title || tool.name }}</div>
          <q-chip v-for="hint in annotationHints(tool)" :key="hint" dense outline color="warning">{{ hint }}</q-chip>
        </div>
        <div v-if="tool.description" class="text-body2 app-muted q-mt-xs">{{ tool.description }}</div>
        <div v-if="tool.title" class="text-caption app-muted q-mt-xs">Technischer Name: {{ tool.name }}</div>
        <div class="text-caption text-warning">Serverangabe, nicht verifiziert</div>
      </q-card-section>
    </q-card>
    <q-inner-loading :showing="servers.loading" />
  </section>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { useAuthStore } from 'stores/auth'
import { useMcpServerStore } from 'stores/mcp-servers'

defineOptions({ name: 'McpAccessPage' })
const route = useRoute()
const auth = useAuthStore()
const servers = useMcpServerStore()
const scope = computed(() => resolveSettingsScope(route.query.scope, auth.isAdmin))
const server = computed(() => servers.servers.find((item) => item.id === String(route.params.serverId || '')))

function annotationHints(tool) {
  const annotations = tool.annotations || {}
  return [
    annotations.readOnlyHint === true && 'Als nur lesend markiert',
    annotations.destructiveHint === true && 'Als destruktiv markiert',
    annotations.openWorldHint === true && 'Externer Datenzugriff laut Server'
  ].filter(Boolean)
}

async function load() {
  servers.selectScope(scope.value)
  if (!servers.loaded) await servers.fetchServers().catch(() => {})
}

watch(scope, load, { immediate: true })
</script>
