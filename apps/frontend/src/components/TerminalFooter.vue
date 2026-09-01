<template>
  <div class="row items-center no-wrap q-px-sm q-py-xs">
    <div v-if="agent" class="col row items-center no-wrap text-caption app-muted">
      <q-icon name="computer" size="18px" class="q-mr-xs" />
      <span class="ellipsis">{{ agent.alias || agent.computerName }}</span>
      <q-spinner-dots
        v-if="terminal.status === 'connecting'"
        class="q-ml-xs"
        color="primary"
        size="20px"
      />
    </div>
    <q-space v-else />
    <div class="col-auto row items-center no-wrap q-ml-sm">
      <q-toggle
        :model-value="terminal.attached"
        :disable="connectionDisabled"
        color="primary"
        dense
        label="Agent verbinden"
        @update:model-value="toggleAttachment"
      />
      <q-spinner-dots
        v-if="['attach', 'detach'].includes(terminal.submitting)"
        class="q-ml-xs"
        color="primary"
        size="20px"
      />
    </div>
    <q-select
      class="terminal-footer__mode q-ml-sm"
      :model-value="terminal.mode"
      :options="modeOptions"
      :disable="modeDisabled"
      :loading="terminal.submitting === 'mode'"
      outlined
      dense
      emit-value
      map-options
      options-dense
      label="Modus"
      @update:model-value="terminal.setMode"
    />
    <q-btn
      v-if="terminal.commandRunning"
      class="q-ml-xs"
      flat
      round
      dense
      color="negative"
      icon="stop_circle"
      aria-label="Agent-Befehl abbrechen"
      @click="stopCommand"
    >
      <q-tooltip>Agent-Befehl abbrechen</q-tooltip>
    </q-btn>
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
const modeOptions = [
  { label: 'Automatisch', value: 'auto' },
  { label: 'Bestätigen', value: 'confirm' }
]
const connectionDisabled = computed(() => (
  terminal.status !== 'open' || terminal.interactionLocked
))
const modeDisabled = computed(() => !terminal.attached || terminal.interactionLocked)

function toggleAttachment(attached) {
  if (attached) terminal.attach(chat.current?.messages, chat.processOutputs)
  else terminal.detach()
}

function stopCommand() {
  void chat.stop()
}
</script>

<style scoped>
.terminal-footer__mode {
  flex: 0 1 11rem;
  max-width: 40%;
  min-width: 0;
}
</style>
