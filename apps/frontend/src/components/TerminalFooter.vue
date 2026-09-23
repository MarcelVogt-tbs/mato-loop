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
        :disable="attachmentDisabled"
        color="primary"
        dense
        label="Agent verbinden"
        @update:model-value="toggleAttachment"
      >
        <q-tooltip>Der Agent übernimmt das Terminal exklusiv. Die geltenden Freigaben bleiben unverändert.</q-tooltip>
      </q-toggle>
    </div>
    <div v-if="runtimeStatus" class="text-caption app-muted" role="status" aria-live="polite">
      {{ runtimeStatus }}
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
const attachmentDisabled = computed(() => !terminal.controlsEnabled || Boolean(terminal.submitting)
  || (terminal.attached ? Boolean(terminal.activeToolCallId) : terminal.busy))
const runtimeStatus = computed(() => {
  if (!terminal.ready) return 'Terminal nicht verbunden.'
  if (terminal.submitting === 'attach') return 'Agent-Verbindung wird angefordert.'
  if (terminal.submitting === 'detach') return 'Manuelle Übernahme wird angefordert.'
  if (terminal.attached) return terminal.activeToolCallId
    ? 'Der Agent führt einen Befehl aus.'
    : 'Agent verbunden.'
  return terminal.busy ? 'Ein manueller Befehl läuft.' : 'Manuelle Steuerung'
})

function toggleAttachment(attached) {
  if (attached) terminal.attach()
  else terminal.detach()
}
</script>
