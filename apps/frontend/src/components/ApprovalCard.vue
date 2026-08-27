<template>
  <q-card flat class="approval-card app-surface">
    <q-card-section class="row items-start no-wrap q-gutter-md">
      <q-avatar :color="riskColor" text-color="white" :icon="actionIcon" />
      <div class="col">
        <div class="row items-center q-gutter-sm">
          <div class="text-subtitle1 text-weight-bold">{{ cardTitle }}</div>
          <q-chip v-if="!isMemoryApproval || isMemoryRemove" dense square :color="riskColor" text-color="white">{{ riskLabel }}</q-chip>
        </div>
        <div class="text-body2 app-muted q-mt-xs">{{ cardSummary }}</div>
      </div>
    </q-card-section>

    <q-card-section class="q-pt-none">
      <template v-if="isMemoryApproval">
        <q-banner v-if="invalidMemoryApproval" rounded class="bg-negative text-white q-mb-md" role="alert">
          Dieser Memory-Vorschlag ist unvollständig und kann nicht genehmigt werden.
        </q-banner>
        <div class="text-caption app-muted q-mb-xs">{{ memoryTargetLabel }}</div>
        <div
          v-for="(memory, index) in memoryTargets"
          :key="memory.id || index"
          class="approval-resource q-mb-sm"
        >
          <div class="text-body2">{{ memory.content }}</div>
          <div class="row items-center q-gutter-xs q-mt-sm">
            <q-chip dense square>{{ memoryCategoryLabel(memory.category) }}</q-chip>
            <q-chip dense square :icon="memoryAudienceIcon(memory.audience)">
              {{ memoryAudienceLabel(memory.audience) }}
            </q-chip>
            <q-chip dense square :icon="memory.pinned ? 'push_pin' : 'bookmark_border'">
              {{ memory.pinned ? 'Immer im Kontext' : 'Bei passender Anfrage' }}
            </q-chip>
          </div>
        </div>
        <q-banner v-if="isMemoryRemove" rounded class="bg-negative text-white q-mt-md" role="alert">
          <template #avatar><q-icon name="delete_forever" color="white" /></template>
          Diese Erinnerung wird dauerhaft gelöscht. Dieser Vorgang kann nicht rückgängig gemacht werden.
        </q-banner>
        <div v-if="approval.actionKind !== 'memory_pin' && memoryPayload.after" class="q-mt-md">
          <div class="text-caption app-muted q-mb-xs">Vorgeschlagenes Ergebnis</div>
          <div class="approval-resource">
            <div class="text-body2">{{ memoryPayload.after.content }}</div>
            <div class="row items-center q-gutter-xs q-mt-sm">
              <q-chip dense square>{{ memoryCategoryLabel(memoryPayload.after.category) }}</q-chip>
              <q-chip dense square :icon="memoryAudienceIcon(memoryPayload.after.audience)">
                {{ memoryAudienceLabel(memoryPayload.after.audience) }}
              </q-chip>
            </div>
          </div>
        </div>
        <q-banner v-else-if="approval.actionKind === 'memory_pin'" rounded class="app-surface-tonal q-mt-md">
          <template #avatar><q-icon name="push_pin" color="primary" /></template>
          Die Erinnerung soll künftig
          auf „{{ memoryPayload.requestedPin ? 'Immer im Kontext' : 'Bei passender Anfrage' }}“ gesetzt werden.
        </q-banner>
      </template>
      <template v-else>
        <div class="approval-resource">
          <div class="text-caption app-muted">{{ resource.label }}</div>
          <code>{{ resource.value }}</code>
          <div v-if="approval.resourceType === 'session' && approval.resourceScope">
            <q-btn
              flat
              dense
              no-caps
              color="primary"
              icon="open_in_new"
              label="Session öffnen"
              :to="{ name: 'chat', query: { session: approval.resourceScope } }"
              target="_blank"
              rel="noopener"
              class="q-mt-sm"
            />
          </div>
          <ToolPresentation
            v-if="presentation.items.length"
            :items="presentation.items"
            :kind="approvalPresentationKind"
            embedded
            class="q-mt-md"
          />
          <div v-if="shellClassification" class="q-mt-md">
            <q-chip
              dense
              square
              :color="shellClassification === 'write' ? 'warning' : 'primary'"
              text-color="white"
              :label="shellClassification === 'write' ? 'Schreibender Befehl' : 'Lesender Befehl'"
            />
            <div v-if="shellPrefixes.length" class="q-mt-sm">
              <div class="text-caption app-muted">Session-Präfixe</div>
              <code v-for="prefix in shellPrefixes" :key="prefix" class="q-mr-sm">{{ prefix }}</code>
            </div>
          </div>
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
      </template>
    </q-card-section>

    <q-separator />
    <q-card-actions class="q-pa-md">
      <div v-if="!isMemoryApproval && hasBothAllowDecisions" class="column">
        <q-checkbox
          v-model="rememberForSession"
          color="primary"
          label="Passende Aktionen in dieser Session nicht mehr nachfragen"
          :disable="Boolean(loadingDecision) || matchingDraftDirty"
        />
        <div class="text-caption app-muted q-ml-sm">
          Kann in den Session-Freigaben widerrufen werden.
        </div>
      </div>
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
        v-if="hasDecision('approve')"
        unelevated
        no-caps
        :color="isMemoryRemove ? 'negative' : 'primary'"
        :label="isMemoryRemove ? 'Entfernen genehmigen' : 'Genehmigen'"
        :loading="loadingDecision === 'approve'"
        :disable="Boolean(loadingDecision) || invalidMemoryApproval"
        @click="decide('approve')"
      />
      <q-btn
        v-if="hasDecision('allow_once') && !hasBothAllowDecisions"
        outline
        no-caps
        color="primary"
        label="Einmal erlauben"
        :loading="loadingDecision === 'allow_once'"
        :disable="Boolean(loadingDecision) || matchingDraftDirty"
        @click="decide('allow_once')"
      />
      <q-btn
        v-if="hasDecision('allow_session') && !hasBothAllowDecisions"
        unelevated
        no-caps
        color="primary"
        label="Für Session erlauben"
        :loading="loadingDecision === 'allow_session'"
        :disable="Boolean(loadingDecision) || matchingDraftDirty"
        @click="decide('allow_session')"
      />
      <q-btn
        v-if="hasBothAllowDecisions"
        unelevated
        no-caps
        color="primary"
        :label="rememberForSession ? 'Für Session erlauben' : 'Einmal erlauben'"
        :loading="loadingDecision === allowDecision"
        :disable="Boolean(loadingDecision) || matchingDraftDirty"
        @click="decide(allowDecision)"
      />
    </q-card-actions>
  </q-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import ToolPresentation from 'components/ToolPresentation.vue'
import { approvalResource, memoryApprovalDisplay, technicalDetailsDisplay } from 'src/utils/approval-display'
import {
  approvalPresentationKind as resolveApprovalPresentationKind,
  toolArgumentsDisplay,
  toolPresentation
} from 'src/utils/tool-display'
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
const rememberForSession = ref(false)
const shellClassification = computed(() => {
  const value = props.approval.payload?.shellClassification
  return ['read', 'write'].includes(value) ? value : null
})
const shellPrefixes = computed(() => (
  Array.isArray(props.approval.payload?.shellPrefixes)
    ? props.approval.payload.shellPrefixes
      .filter((prefix) => Array.isArray(prefix) && prefix.every((token) => typeof token === 'string'))
      .map((prefix) => prefix.join(' '))
    : []
))
const isMemoryApproval = computed(() => props.approval.requestType === 'memory_change')
const memoryDisplay = computed(() => memoryApprovalDisplay(props.approval))
const memoryPayload = computed(() => memoryDisplay.value.payload)
const memoryTargets = computed(() => memoryDisplay.value.targets)
const invalidMemoryApproval = computed(() => isMemoryApproval.value && memoryDisplay.value.invalid)
const isMemoryRemove = computed(() => isMemoryApproval.value && memoryDisplay.value.destructive)
const cardTitle = computed(() => isMemoryApproval.value ? memoryDisplay.value.title : props.approval.title)
const cardSummary = computed(() => isMemoryApproval.value ? memoryDisplay.value.summary : props.approval.summary)
const memoryTargetLabel = computed(() => memoryDisplay.value.targetLabel)
const riskColor = computed(() => isMemoryRemove.value || props.approval.riskLevel === 'high' ? 'negative' : 'warning')
const riskLabel = computed(() => isMemoryRemove.value
  ? 'Hoch'
  : ({ high: 'Hoch', medium: 'Mittel', low: 'Niedrig' })[props.approval.riskLevel] || props.approval.riskLevel)
const actionIcon = computed(() => {
  if (isMemoryApproval.value) return memoryDisplay.value.icon
  if (props.approval.capability?.startsWith('filesystem')) return 'folder_managed'
  if (props.approval.capability === 'execution.directory') return 'drive_file_move'
  if (props.approval.capability === 'system.info') return 'computer'
  if (props.approval.capability === 'process.execute') return 'terminal'
  if (props.approval.capability?.startsWith('git.')) return 'account_tree'
  if (props.approval.capability === 'skills.load') return 'school'
  if (props.approval.actionKind === 'edit_skill') return 'edit_note'
  if (props.approval.capability === 'skills.write' || props.approval.actionKind === 'build_skill') return 'auto_awesome'
  if (props.approval.capability === 'integration.private_data.release') return 'cloud_upload'
  if (props.approval.capability?.startsWith('automation.')) return 'schedule'
  if (props.approval.resourceType === 'url') return 'language'
  if (props.approval.resourceType === 'session_collection') return 'folder_shared'
  if (props.approval.resourceType === 'session') return 'forum'
  return 'approval'
})
const resource = computed(() => approvalResource(props.approval, chat.cachedSessions))
const skillDraftApproval = computed(() => (
  props.approval.actionKind === 'build_skill' || props.approval.actionKind === 'edit_skill'
))
const approvalTool = computed(() => ({
  name: props.approval.details?.tool,
  arguments: props.approval.details?.arguments
}))
const approvalPresentationKind = computed(() => resolveApprovalPresentationKind(approvalTool.value))
const presentation = computed(() => toolPresentation(
  approvalTool.value,
  props.approval.details?.displayItems
))
const skillPreview = computed(() => skillDraftApproval.value
  ? props.approval.details?.skillPreview
  : null)
const draftFile = computed(() => skillDraftApproval.value
  ? props.approval.details?.draftFile
  : null)
const matchingDraftDirty = computed(() => Boolean(
  draftFile.value
  && workspace.editorDirty
  && workspace.sessionId === props.approval.sessionId
  && workspace.scope === 'session'
  && !workspace.toolContext
  && workspace.viewedEntry?.path === draftFile.value.path
))
const draftRevision = computed(() => draftFile.value
  ? workspace.filePreviews[draftFile.value.path]?.revision ?? draftFile.value.initialRevision
  : undefined)
const technicalDetails = computed(() => {
  if (approvalTool.value.name === 'memory_write') {
    return toolArgumentsDisplay(approvalTool.value.arguments, approvalTool.value.name)
  }
  const details = { ...props.approval.details }
  delete details.displayItems
  return technicalDetailsDisplay(details)
})
const hasBothAllowDecisions = computed(() => (
  hasDecision('allow_once') && hasDecision('allow_session')
))
const allowDecision = computed(() => (
  rememberForSession.value ? 'allow_session' : 'allow_once'
))

function hasDecision(decision) {
  return Array.isArray(props.approval.availableDecisions)
    && props.approval.availableDecisions.includes(decision)
}

function memoryCategoryLabel(value) {
  return ({
    context: 'Kontext',
    fact: 'Fakt',
    goal: 'Ziel',
    identity: 'Identität',
    preference: 'Präferenz'
  })[value] || value || 'Ohne Kategorie'
}

function memoryAudienceLabel(value) {
  return ({
    private_only: 'Nur private Modelle',
    public_only: 'Nur öffentliche Modelle',
    both: 'Öffentliche und private Modelle'
  })[value] || value || 'Ohne Modellzugriff'
}

function memoryAudienceIcon(value) {
  return ({ private_only: 'lock', public_only: 'public', both: 'groups' })[value] || 'help_outline'
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
