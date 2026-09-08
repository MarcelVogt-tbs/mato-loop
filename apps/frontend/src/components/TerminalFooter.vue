<template>
  <div class="column no-wrap q-px-sm q-py-xs">
    <div class="row items-center q-gutter-xs">
      <div v-if="agent" class="col-12 col-sm row items-center no-wrap text-caption app-muted">
        <q-icon name="computer" size="18px" class="q-mr-xs" />
        <span class="ellipsis">{{ agent.alias || agent.computerName }}</span>
        <q-spinner-dots v-if="['connecting', 'acquiring', 'syncing'].includes(terminal.status)" class="q-ml-xs" color="primary" size="20px" />
      </div>
      <q-toggle
        class="col-auto"
        :model-value="terminal.attached"
        :disable="connectionDisabled"
        color="primary"
        dense
        label="Agent verbinden"
        @update:model-value="toggleAttachment"
      >
        <q-tooltip>Agent-Befehle verwenden diese Shell. Die geltenden Freigaben bleiben unverändert.</q-tooltip>
      </q-toggle>
      <q-spinner-dots v-if="terminal.submitting" color="primary" size="20px" />
      <q-btn
        v-if="chat.running"
        class="col-auto"
        flat dense no-caps
        color="negative"
        icon="stop_circle"
        label="Agent stoppen"
        :loading="chat.stopping"
        :disable="chat.stopping || terminal.status === 'replaced'"
        @click="stopAgent"
      >
        <q-tooltip>Den gesamten Agent-Lauf stoppen, nicht nur den Vordergrundprozess.</q-tooltip>
      </q-btn>
    </div>
    <div class="text-caption app-muted ellipsis q-mt-xs" :title="terminal.currentDirectory || ''">
      Terminal-Ordner: {{ terminal.currentDirectory || 'Noch nicht bekannt' }}
      <q-tooltip v-if="terminal.currentDirectory">{{ terminal.currentDirectory }} · Unabhängig vom Arbeitsordner der Session.</q-tooltip>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useChatStore } from 'stores/chat'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useTerminalStore } from 'stores/terminal'

defineOptions({ name: 'TerminalFooter' })

const chat = useChatStore()
const executionAgents = useExecutionAgentStore()
const terminal = useTerminalStore()
const agent = computed(() => executionAgents.byId(chat.current?.preferredExecutionAgentId))
const connectionDisabled = computed(() => !terminal.inputEnabled || Boolean(terminal.submitting)
  || (terminal.attached && Boolean(terminal.activeToolCallId || terminal.pending)))

function toggleAttachment(attached) {
  if (attached) terminal.attach()
  else terminal.detach()
}

function stopAgent() {
  if (chat.running && !chat.stopping && terminal.status !== 'replaced') void chat.stop()
}
</script>
