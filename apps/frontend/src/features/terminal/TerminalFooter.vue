<template>
  <div class="row items-center q-gutter-xs q-px-sm q-py-xs">
    <div v-if="agent" class="col-12 col-sm row items-center no-wrap text-caption app-muted">
      <q-icon name="computer" size="18px" class="q-mr-xs" />
      <span class="col ellipsis">{{ agent.alias || agent.computerName }}</span>
      <q-spinner-dots v-if="['connecting', 'acquiring', 'syncing'].includes(terminal.status)" class="q-ml-xs" color="primary" size="20px" />
      <span v-if="terminal.controlsEnabled && (terminal.attached || terminal.activeToolCallId)" class="col-auto q-ml-sm text-primary" role="status" aria-live="polite">
        {{ terminal.activeToolCallId ? 'Agent aktiv' : 'Agent verbunden' }}
      </span>
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

function toggleAttachment(attached) {
  if (attached) terminal.attach()
  else terminal.detach()
}
</script>
