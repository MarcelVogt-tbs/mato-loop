<template>
  <section v-if="entries.length" ref="element" aria-label="Offene Anfragen">
    <q-resize-observer @resize="emit('rendered')" />
    <component
      :is="entry.kind === 'approval' ? ApprovalCard : QuestionDock"
      v-for="entry in entries"
      v-show="entry.id === selectedId"
      :key="entry.id"
      :data-request-id="entry.id"
      v-bind="entry.kind === 'approval' ? { approval: entry.request } : { request: entry.request }"
      @open-approval-draft="emit('open-approval-draft', $event)"
      @rendered="emit('rendered')"
    >
      <template #navigation>
        <RequestProgress
          v-if="entries.length > 1 || entry.request.payload?.subagentSessionId"
          label="Anfrage"
          :icon="entry.kind === 'approval' ? 'approval' : 'help_outline'"
          :items="items"
          :index="index"
          :disabled="busy"
          @select="select"
        >
          <SubagentRequestLink :request="entry.request" />
        </RequestProgress>
      </template>
    </component>
  </section>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import ApprovalCard from 'src/features/approvals/ApprovalCard.vue'
import QuestionDock from 'src/features/questions/QuestionDock.vue'
import SubagentRequestLink from 'src/features/subagents/SubagentRequestLink.vue'
import RequestProgress from 'src/shared/ui/RequestProgress.vue'
import { useApprovalStore } from 'stores/approvals'
import { useQuestionStore } from 'stores/questions'

const props = defineProps({
  approvals: { type: Array, default: () => [] },
  questions: { type: Array, default: () => [] }
})
const emit = defineEmits(['open-approval-draft', 'rendered'])
const approvalStore = useApprovalStore()
const questionStore = useQuestionStore()
const busy = computed(() => approvalStore.submittingId !== null || questionStore.submittingId !== null)
const element = ref(null)
const selectedId = ref(null)
const entries = computed(() => [
  ...props.approvals.map((request) => ({ id: `approval:${request.id}`, kind: 'approval', request })),
  ...props.questions.map((request) => ({ id: `question:${request.id}`, kind: 'question', request }))
].sort((a, b) => (a.request.createdAt || '').localeCompare(b.request.createdAt || '') || a.id.localeCompare(b.id)))
const index = computed(() => entries.value.findIndex((entry) => entry.id === selectedId.value))
const items = computed(() => entries.value.map((entry) => ({
  id: entry.id,
  label: `${entry.kind === 'approval' ? 'Freigabe' : 'Rückfrage'} · ${entry.request.payload?.subagentTitle || 'Hauptagent'}`
})))

watch(entries, (next, previous = []) => {
  if (next.some((entry) => entry.id === selectedId.value)) return
  const oldIndex = previous.findIndex((entry) => entry.id === selectedId.value)
  selectedId.value = next[Math.max(0, Math.min(oldIndex, next.length - 1))]?.id || null
}, { immediate: true })

function select(position) {
  selectedId.value = entries.value[position].id
  void nextTick(() => element.value?.querySelector(`[data-request-id="${selectedId.value}"] [aria-current="step"]`)?.focus())
}
</script>
