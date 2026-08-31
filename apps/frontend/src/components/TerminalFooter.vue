<template>
  <div class="row items-center no-wrap q-px-sm q-py-xs">
    <div class="col-auto row items-center no-wrap">
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
    <q-space />
    <q-select
      class="terminal-footer__mode"
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
import { useTerminalStore } from 'stores/terminal'

defineOptions({ name: 'TerminalFooter' })

const chat = useChatStore()
const terminal = useTerminalStore()
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
