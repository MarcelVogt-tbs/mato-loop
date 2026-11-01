<template>
  <div v-if="children.length" class="column items-start" role="list" aria-label="Subagenten">
    <AgentActivityRow
      v-for="child in children"
      :key="child.id"
      :to="{ name: 'chat', query: { ...route.query, session: child.parentSessionId, subagent: child.id } }"
      :aria-label="`${child.title || 'Subagent'} · ${subagentStatus(child.run)}`"
      class="subagent-row tool-call tool-call__header"
    >
      <svg
        class="work-history__activity-icon"
        :class="isActiveSubagent(child) ? 'text-primary' : 'text-grey'"
        width="18" height="18" viewBox="0 0 18 18" fill="none"
        focusable="false" aria-hidden="true"
      >
        <rect
          x="4.75" y="4.75" width="8.5" height="8.5" rx="1.5" stroke="currentColor" stroke-width="1.5"
          :opacity="isActiveSubagent(child) && child.run?.status !== 'running' ? .4 : 1"
          :class="{ 'subagent-row__pulse': child.run?.status === 'running' && !reducedMotion }"
        />
      </svg>
      <span
        class="work-history__activity-title tool-call__title ellipsis"
        :class="{ 'work-history__activity-title--past': child.run?.status !== 'running' }"
      >{{ child.title || 'Subagent' }}</span>
      <span class="work-history__activity-summary tool-call__summary">{{ labels[child.run?.status] || 'Wartet' }}</span>
      <q-icon class="work-history__detail-indicator" name="chevron_right" size="17px" aria-hidden="true" />
    </AgentActivityRow>
  </div>
</template>

<script setup>
import { isActiveSubagent, subagentStatus } from 'stores/subagents'
import AgentActivityRow from 'src/features/chat/components/AgentActivityRow.vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const labels = { running: 'Arbeitet', completed: 'Fertig', failed: 'Abgebrochen', cancelled: 'Abgebrochen' }
defineProps({ children: { type: Array, default: () => [] } })
</script>

<style scoped>
.subagent-row {
  width: min(20rem, 100%);
}

.subagent-row :deep(.work-history__activity-line) {
  /* Keep the status visible when a long task title truncates. */
  grid-template-columns: auto minmax(0, max-content) minmax(max-content, 1fr) auto;
}

.subagent-row__pulse {
  animation: subagent-pulse 2.8s ease-in-out infinite;
}

@keyframes subagent-pulse {
  0%, 100% { opacity: .4; }
  50% { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .subagent-row__pulse {
    animation: none;
  }
}
</style>
