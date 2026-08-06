<template>
  <q-card flat class="approval-card app-surface">
    <q-card-section class="row items-start no-wrap q-gutter-md">
      <q-avatar :color="riskColor" text-color="white" :icon="actionIcon" />
      <div class="col">
        <div class="row items-center q-gutter-sm">
          <div class="text-subtitle1 text-weight-bold">{{ approval.title }}</div>
          <q-chip dense square :color="riskColor" text-color="white">{{ riskLabel }}</q-chip>
        </div>
        <div class="text-body2 app-muted q-mt-xs">{{ approval.summary }}</div>
      </div>
    </q-card-section>

    <q-card-section class="q-pt-none">
      <div class="approval-resource">
        <div class="text-caption app-muted">{{ resource.label }}</div>
        <code>{{ resource.value }}</code>
      </div>
      <div v-if="memoryContent" class="approval-resource q-mt-md">
        <div class="text-caption app-muted">Memory-Inhalt</div>
        <pre class="approval-details q-mt-xs">{{ memoryContent }}</pre>
      </div>
      <q-banner
        v-if="approval.capability === 'integration.private_data.release'"
        rounded
        class="app-surface-tonal q-mt-md"
      >
        Private Daten aus Integrationen dürfen an das öffentliche Modell dieser Session
        übertragen werden. Ein späterer Widerruf kann bereits übermittelte Daten nicht
        zurückholen.
      </q-banner>
      <q-expansion-item
        v-if="technicalDetails"
        dense
        icon="data_object"
        label="Technische Details"
        class="q-mt-md"
      >
        <pre class="approval-details">{{ technicalDetails }}</pre>
      </q-expansion-item>
    </q-card-section>

    <q-separator />
    <q-card-actions class="q-pa-md">
      <q-space />
      <q-btn
        v-if="hasDecision('deny')"
        flat
        no-caps
        color="negative"
        label="Ablehnen"
        :loading="loadingDecision === 'deny'"
        :disable="Boolean(loadingDecision)"
        @click="decide('deny')"
      />
      <q-btn
        v-if="hasDecision('allow_once')"
        outline
        no-caps
        color="primary"
        label="Einmal erlauben"
        :loading="loadingDecision === 'allow_once'"
        :disable="Boolean(loadingDecision)"
        @click="decide('allow_once')"
      />
      <q-btn
        v-if="hasDecision('allow_session')"
        unelevated
        no-caps
        color="primary"
        label="Für Session erlauben"
        :loading="loadingDecision === 'allow_session'"
        :disable="Boolean(loadingDecision)"
        @click="decide('allow_session')"
      />
    </q-card-actions>
  </q-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import { approvalResource, formatTechnicalDetails } from 'src/utils/approval-display'
import { formatToolArguments, memoryWriteContent } from 'src/utils/tool-display'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'

defineOptions({ name: 'ApprovalCard' })

const props = defineProps({ approval: { type: Object, required: true } })
const approvals = useApprovalStore()
const chat = useChatStore()
const loadingDecision = ref(null)
const riskColor = computed(() => props.approval.riskLevel === 'high' ? 'negative' : 'warning')
const riskLabel = computed(() => ({ high: 'Hoch', medium: 'Mittel', low: 'Niedrig' })[props.approval.riskLevel] || props.approval.riskLevel)
const actionIcon = computed(() => {
  if (props.approval.capability?.startsWith('filesystem')) return 'folder_managed'
  if (props.approval.capability === 'integration.private_data.release') return 'cloud_upload'
  if (props.approval.resourceType === 'url') return 'language'
  if (props.approval.resourceType === 'session_collection') return 'folder_shared'
  if (props.approval.resourceType === 'session') return 'forum'
  return 'approval'
})
const resource = computed(() => approvalResource(props.approval, chat.sessions))
const approvalTool = computed(() => ({
  name: props.approval.details?.tool,
  arguments: props.approval.details?.arguments
}))
const memoryContent = computed(() => memoryWriteContent(approvalTool.value))
const technicalDetails = computed(() => memoryContent.value
  ? formatToolArguments(approvalTool.value.arguments, approvalTool.value.name)
  : formatTechnicalDetails(props.approval.details))

function hasDecision(decision) {
  return props.approval.availableDecisions.includes(decision)
}

async function decide(decision) {
  loadingDecision.value = decision
  try {
    await approvals.decide(props.approval.id, decision)
  } finally {
    loadingDecision.value = null
  }
}
</script>
