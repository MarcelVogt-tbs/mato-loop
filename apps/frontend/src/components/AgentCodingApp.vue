<template>
  <section class="column full-height no-wrap" aria-labelledby="agent-coding-title">
    <q-toolbar>
      <q-toolbar-title>
        <h1 id="agent-coding-title" class="text-h6 q-ma-none">Agent-Coding</h1>
      </q-toolbar-title>
      <q-btn
        v-if="$q.screen.lt.md"
        flat
        no-caps
        icon="chat"
        label="Zum Chat"
        @click="returnToChat"
      />
    </q-toolbar>
    <q-separator />
    <q-scroll-area class="col">
      <q-list padding separator aria-label="Coding-Werkzeuge">
        <q-item
          v-for="action in actions"
          :key="action.id"
          clickable
          v-ripple
          @click="open(action.id)"
        >
          <q-item-section avatar><q-icon :name="action.icon" color="primary" /></q-item-section>
          <q-item-section>
            <q-item-label>{{ action.label }}</q-item-label>
            <q-item-label caption>{{ action.caption }}</q-item-label>
          </q-item-section>
          <q-item-section side><q-icon name="chevron_right" /></q-item-section>
        </q-item>
      </q-list>
    </q-scroll-area>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import {
  browserControlCenterAvailable,
  controlCenterIntegrationManifest,
  codingControlCenterAvailable,
  computerFilesControlCenterAvailable,
  terminalControlCenterState
} from 'src/control-center/manifest'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'
import { useProjectStore } from 'stores/projects'

defineOptions({ name: 'AgentCodingApp' })

const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const integrations = useIntegrationStore()
const projects = useProjectStore()
const activeProject = computed(() => projects.items.find(
  (project) => project.id === (chat.current ? chat.current.projectId : chat.sessionsProjectId)
))
const actions = computed(() => [
  { id: 'files', label: 'Session-Dateien', caption: 'Dateien dieser Session öffnen', icon: 'folder_open', available: true },
  { id: 'computer-files', label: 'Computer-Dateien', caption: 'Dateien auf dem verbundenen Computer öffnen', icon: 'folder_shared', available: computerFilesControlCenterAvailable(chat.activeContext, executionAgents) },
  { id: 'terminal', label: 'Terminal', caption: 'Terminal für diese Session öffnen', icon: 'terminal', available: terminalControlCenterState(chat.current, executionAgents).available },
  { ...controlCenterIntegrationManifest.find((item) => item.id === 'browser'), caption: 'Browser auf dem verbundenen Computer öffnen', available: browserControlCenterAvailable(chat.current, executionAgents, integrations) },
  { id: 'coding', label: 'OpenCode', caption: 'Bestehende OpenCode-App öffnen', icon: 'code', available: codingControlCenterAvailable(activeProject.value) }
].filter((action) => action.available))

function open(appId) {
  void controlCenter.openApp(appId).catch(() => {})
}

function returnToChat() {
  void controlCenter.setOpen(false).catch(() => {})
}
</script>
