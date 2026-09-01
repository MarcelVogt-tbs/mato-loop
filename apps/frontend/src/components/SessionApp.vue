<template>
  <section class="column full-height no-wrap" aria-label="Session">
    <q-scroll-area class="col">
      <SessionSettingsFields
        :session="chat.activeContext"
        :disabled="settingsDisabled"
        grouped
        show-warning
        class="q-pa-md"
        @update="chat.updateActive"
      />
    </q-scroll-area>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import SessionSettingsFields from 'components/SessionSettingsFields.vue'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'

defineOptions({ name: 'SessionApp' })

const approvals = useApprovalStore()
const chat = useChatStore()
const settingsDisabled = computed(() => Boolean(
  chat.running
  || chat.runId
  || chat.current?.activeRun
  || chat.sessionSettingsPending > 0
  || (chat.current && approvals.forSession(chat.current.id).length)
))
</script>
