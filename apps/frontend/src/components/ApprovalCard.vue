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
      <div v-if="skillPreview" class="approval-resource q-mt-md">
        <div class="text-caption app-muted">Skill-Vorschau</div>
        <div class="text-subtitle1 text-weight-bold q-mt-xs">{{ skillPreview.name }}</div>
        <div class="text-body2 q-mt-xs">{{ skillPreview.description }}</div>
        <div class="text-caption app-muted q-mt-md">Anweisungen</div>
        <pre class="approval-details q-mt-xs">{{ skillPreview.instructions }}</pre>
        <q-btn
          v-if="draftFile && showDraftAction"
          outline
          no-caps
          color="primary"
          icon="edit_document"
          label="Entwurf öffnen und bearbeiten"
          class="q-mt-md"
          @click="emit('open-approval-draft', approval)"
        />
      </div>
      <q-banner v-if="matchingDraftDirty" rounded class="app-surface-tonal q-mt-md">
        <template #avatar><q-icon name="edit_note" color="warning" /></template>
        Speichere oder verwirf zuerst die Änderungen am Skill-Entwurf.
      </q-banner>
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
        v-if="technicalDetails.text"
        dense
        icon="data_object"
        label="Technische Details"
        class="q-mt-md"
      >
        <pre class="approval-details">{{ showFullDetails ? technicalDetails.fullText : technicalDetails.text }}</pre>
        <q-btn
          v-if="technicalDetails.truncated"
          flat
          dense
          no-caps
          color="primary"
          :label="showFullDetails ? 'Weniger anzeigen' : 'Vollständig anzeigen'"
          class="q-mt-xs"
          @click="showFullDetails = !showFullDetails"
        />
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
        :disable="Boolean(loadingDecision) || matchingDraftDirty"
        @click="decide('allow_once')"
      />
      <q-btn
        v-if="hasDecision('allow_session')"
        unelevated
        no-caps
        color="primary"
        label="Für Session erlauben"
        :loading="loadingDecision === 'allow_session'"
        :disable="Boolean(loadingDecision) || matchingDraftDirty"
        @click="decide('allow_session')"
      />
    </q-card-actions>
  </q-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import { approvalResource, technicalDetailsDisplay } from 'src/utils/approval-display'
import { memoryWriteContent, toolArgumentsDisplay } from 'src/utils/tool-display'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'ApprovalCard' })

const props = defineProps({
  approval: { type: Object, required: true },
  showDraftAction: { type: Boolean, default: true }
})
const emit = defineEmits(['open-approval-draft'])
const approvals = useApprovalStore()
const chat = useChatStore()
const workspace = useWorkspaceStore()
const loadingDecision = ref(null)
const showFullDetails = ref(false)
const riskColor = computed(() => props.approval.riskLevel === 'high' ? 'negative' : 'warning')
const riskLabel = computed(() => ({ high: 'Hoch', medium: 'Mittel', low: 'Niedrig' })[props.approval.riskLevel] || props.approval.riskLevel)
const actionIcon = computed(() => {
  if (props.approval.capability?.startsWith('filesystem')) return 'folder_managed'
  if (props.approval.capability === 'execution.directory') return 'drive_file_move'
  if (props.approval.capability === 'system.info') return 'computer'
  if (props.approval.capability === 'process.execute') return 'terminal'
  if (props.approval.capability?.startsWith('git.')) return 'account_tree'
  if (props.approval.capability === 'skills.load') return 'school'
  if (props.approval.capability === 'skills.write' || props.approval.actionKind === 'build_skill') return 'auto_awesome'
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
const skillPreview = computed(() => props.approval.actionKind === 'build_skill'
  ? props.approval.details?.skillPreview
  : null)
const draftFile = computed(() => props.approval.actionKind === 'build_skill'
  ? props.approval.details?.draftFile
  : null)
const matchingDraftDirty = computed(() => Boolean(
  draftFile.value
  && workspace.editorDirty
  && workspace.sessionId === props.approval.sessionId
  && workspace.scope === 'session'
  && !workspace.rootIdentity
  && workspace.viewedEntry?.path === draftFile.value.path
))
const draftRevision = computed(() => draftFile.value
  ? workspace.filePreviews[draftFile.value.path]?.revision ?? draftFile.value.initialRevision
  : undefined)
const technicalDetails = computed(() => memoryContent.value
  ? toolArgumentsDisplay(approvalTool.value.arguments, approvalTool.value.name)
  : technicalDetailsDisplay(props.approval.details))

function hasDecision(decision) {
  return props.approval.availableDecisions.includes(decision)
}

async function decide(decision) {
  loadingDecision.value = decision
  try {
    await approvals.decide(props.approval.id, decision, draftRevision.value)
  } finally {
    loadingDecision.value = null
  }
}
</script>
