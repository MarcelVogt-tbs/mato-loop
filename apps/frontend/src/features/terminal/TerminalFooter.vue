<template>
  <div class="row items-center q-gutter-xs q-px-sm q-py-xs">
    <div v-if="agent" class="col-12 col-sm row items-center no-wrap text-caption app-muted">
      <q-icon name="computer" size="18px" class="q-mr-xs" />
      <span class="col ellipsis">{{ agentDisplayName(agent) }}</span>
      <q-spinner-dots v-if="['connecting', 'acquiring', 'syncing'].includes(terminal.status)" class="q-ml-xs" color="primary" size="20px" />
      <span v-if="terminal.controlsEnabled && terminal.activeToolCallId" class="col-auto q-ml-sm text-primary" role="status" aria-live="polite">
        Agent aktiv
      </span>
    </div>
    <q-btn-toggle
      v-model="terminal.mode"
      class="col-auto"
      unelevated
      no-caps
      dense
      toggle-color="primary"
      :options="[{ label: 'Agent-Ausgabe', value: 'agent' }, { label: 'Terminal', value: 'terminal' }]"
      role="group"
      aria-label="Terminalansicht"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { agentDisplayName } from 'src/features/computers/display'
import { useChatStore } from 'src/features/chat/store'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useTerminalStore } from 'stores/terminal'

defineOptions({ name: 'TerminalFooter' })

const chat = useChatStore()
const executionAgents = useExecutionAgentStore()
const terminal = useTerminalStore()
const agent = computed(() => executionAgents.byId(chat.current?.preferredExecutionAgentId))
</script>
