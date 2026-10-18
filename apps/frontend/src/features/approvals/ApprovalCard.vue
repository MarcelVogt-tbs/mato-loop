<template>
  <q-card flat class="approval-card app-surface">
    <SubagentRequestLink :request="approval" />
    <q-card-section class="q-pa-md q-pb-sm">
      <div class="row items-center no-wrap">
        <q-icon :name="actionIcon" :color="isMemoryRemove ? 'negative' : 'primary'" size="24px" class="q-mr-sm" />
        <div class="text-subtitle1 text-weight-bold">{{ cardTitle }}</div>
      </div>
      <div v-if="!isMemoryApproval && approval.reason && approval.reason !== 'Keine Begründung angegeben'" class="text-body2 q-mt-sm">{{ approval.reason }}</div>
    </q-card-section>

    <q-card-section class="q-px-md q-pt-none q-pb-md">
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
            <div class="text-caption app-muted">
              {{ memoryCategoryLabel(memory.category) }} · {{ memoryAudienceLabel(memory.audience) }} ·
              {{ memory.pinned ? 'Immer im Kontext' : 'Bei passender Anfrage' }}
            </div>
          </div>
        </div>
        <q-banner v-if="isMemoryRemove" rounded class="bg-negative text-white q-mt-md" role="alert">
          <template #avatar><q-icon name="delete_forever" color="white" /></template>
          Diese Erinnerung wird dauerhaft gelöscht. Dieser Vorgang kann nicht rückgängig gemacht werden.
        </q-banner>
        <div v-if="approval.actionKind !== 'memory_pin' && memoryPayload.after" class="q-mt-md">
          <div class="row items-center q-gutter-xs q-mb-xs">
            <div class="text-caption app-muted">Neu</div>
            <q-btn
              v-if="canEditMemory && !memoryEdit"
              flat round dense icon="edit" color="primary"
              aria-label="Vorschlag bearbeiten"
              :disable="Boolean(loadingDecision)"
              @click="startMemoryEdit"
            />
            <q-btn
              v-if="memoryEdit"
              flat dense no-caps icon="undo" label="Bearbeitung verwerfen"
              :disable="Boolean(loadingDecision)"
              @click="memoryEdit = null"
            />
          </div>
          <q-form v-if="memoryEdit" ref="memoryFormRef" class="approval-resource" @submit.prevent="decide('approve')">
            <q-input dense
              v-model="memoryEdit.content"
              outlined autofocus autogrow type="textarea" label="Inhalt" maxlength="20000"
              :disable="Boolean(loadingDecision)"
              :rules="[(value) => Boolean(value?.trim()) || 'Bitte einen Inhalt eingeben.']"
            />
            <AppSelect dense
              v-model="memoryEdit.category"
              outlined emit-value map-options label="Kategorie" class="q-mt-sm"
              :options="memoryCategoryOptions" :disable="Boolean(loadingDecision)"
            />
            <AppSelect dense
              v-if="auth.modelTrustEnabled"
              v-model="memoryEdit.audience"
              outlined emit-value map-options label="Verfügbar für" class="q-mt-md"
              hint="Legt fest, welche Modellarten diese Erinnerung verwenden dürfen."
              :options="memoryAudienceOptions" :disable="Boolean(loadingDecision)"
            >
              <template #selected><span>{{ memoryAudienceLabel(memoryEdit.audience) }}</span></template>
            </AppSelect>
            <q-toggle dense
              v-model="memoryEdit.pinned"
              color="primary" label="Immer im Kontext" class="q-mt-md"
              :disable="Boolean(loadingDecision)"
            />
            <div class="text-caption app-muted">
              Ausgeschaltet bleibt die Erinnerung gespeichert und erscheint automatisch nur bei einer passenden Anfrage; vollständige Details bleiben gezielt abrufbar.
            </div>
          </q-form>
          <div v-else class="approval-resource">
            <div class="text-body2">{{ memoryPayload.after.content }}</div>
            <div class="row items-center q-gutter-xs q-mt-sm">
              <div class="text-caption app-muted">
                {{ memoryCategoryLabel(memoryPayload.after.category) }} · {{ memoryAudienceLabel(memoryPayload.after.audience) }} ·
                {{ memoryPinned ? 'Immer im Kontext' : 'Bei passender Anfrage' }}
              </div>
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
        <div class="approval-content">
          <div v-if="!resourceInGrant || computer" class="text-body2 app-muted">
            <span v-if="computer">{{ computer }}<span v-if="!resourceInGrant"> · </span></span>
            <component v-if="!resourceInGrant" :is="resource.format === 'code' ? 'code' : 'span'" :aria-label="resource.label">{{ resource.value }}</component>
          </div>
          <div v-if="approval.capability === 'session.read' && approval.resourceType === 'session' && approval.resourceScope">
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
          <div v-if="presentation.items.length || canEditArguments" class="approval-action app-surface-tonal q-pa-sm q-mt-sm">
            <div class="row items-start no-wrap q-gutter-sm">
              <div class="col">
                <ToolPresentation
                  v-if="presentation.items.length"
                  :items="presentation.items"
                  :kind="approvalPresentationKind"
                />
              </div>
              <q-btn
                v-if="canEditArguments"
                flat round dense
                :icon="argumentEdit ? 'undo' : 'edit'"
                :aria-label="argumentEdit ? 'Bearbeitung verwerfen' : 'Argumente bearbeiten'"
                :disable="Boolean(loadingDecision)"
                @click="argumentEdit ? argumentEdit = null : startArgumentEdit()"
              />
            </div>
          </div>
          <q-form v-if="argumentEdit" ref="argumentFormRef" class="q-mt-sm">
            <q-input dense
              v-for="field in argumentEdit"
              :key="field.path.join('.')"
              v-model="field.value"
              outlined
              :type="field.inputType"
              :label="field.label"
              :autogrow="field.inputType === 'textarea'"
              :maxlength="field.maxLength"
              :disable="Boolean(loadingDecision)"
              :rules="argumentRules(field)"
              class="q-mb-sm"
            />
          </q-form>
          <div v-if="actionDisplay?.incomplete && !argumentEditDirty" class="text-caption q-mt-sm">Analyse unvollständig: Nicht alle Befehle und Pfade konnten geprüft werden.</div>
        </div>
        <div v-if="skillPreview" class="approval-resource q-mt-md">
          <div class="text-caption app-muted">Skill-Vorschau</div>
          <q-badge :label="skillPreview.scope === 'instance' ? 'Global – für alle Benutzer' : 'Persönlich'" class="q-mt-xs" />
          <div class="text-subtitle1 text-weight-bold q-mt-xs">{{ skillPreview.name }}</div>
          <div class="text-body2 q-mt-xs">{{ skillPreview.description }}</div>
          <div class="text-caption app-muted q-mt-md">Anweisungen</div>
          <pre class="approval-details q-mt-xs">{{ skillPreview.instructions }}</pre>
          <div class="text-caption q-mt-sm">Paketdateien: {{ skillPreview.files?.join(', ') }}</div>
          <q-btn flat no-caps icon="refresh" label="Paketvorschau aktualisieren" :loading="reviewingSkill" @click="reviewSkill" />
          <q-btn
            v-if="draftFile && showDraftAction"
            outline
            no-caps
            color="primary"
            :icon="childSkillDraft ? 'visibility' : 'edit_document'"
            :label="childSkillDraft ? 'Entwurf ansehen' : 'Entwurf öffnen und bearbeiten'"
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
      </template>
    </q-card-section>

    <q-card-section v-if="!isMemoryApproval && (hasDecision('allow_once') || hasDecision('allow_session'))" class="q-px-md q-py-none">
      <div class="approval-grant row items-center q-mb-sm">
        <app-select
          v-if="grantOptions.length > 1 && !argumentEditDirty"
          v-model="grantOption"
          outlined
          emit-value
          map-options
          option-value="id"
          label="Freigabe für"
          :options="grantOptions"
          :disable="Boolean(loadingDecision) || matchingDraftDirty"
          class="approval-grant__scope"
        >
          <template #selected><span class="col">{{ grantDisplay.label }}</span></template>
        </app-select>
        <div v-else-if="grantDisplay.label" class="text-body2">
          <span class="app-muted">Freigabe für:</span> {{ grantDisplay.label }}
        </div>
        <div
          v-if="(actionDisplay && !argumentEditDirty) || approval.resourceType === 'execution_administrator'"
          class="row items-center"
          role="group"
          aria-label="Angefragte Rechte"
        >
          <template v-if="!argumentEditDirty">
            <q-chip
              v-for="chip in actionDisplay?.chips || []"
              :key="chip.label"
              dense outline size="11px" class="q-my-none"
              :style="{ color: $q.dark.isActive ? chip.darkColor : chip.color }"
            >{{ chip.label }}</q-chip>
          </template>
          <q-chip
            v-if="approval.resourceType === 'execution_administrator'"
            dense outline size="11px" class="q-my-none"
            :style="{ color: $q.dark.isActive ? '#c4b286' : '#817047' }"
          >Administratorrechte</q-chip>
        </div>
      </div>
    </q-card-section>
    <q-card-actions class="q-px-md q-pt-sm q-pb-md">
      <q-checkbox dense
        v-if="!isMemoryApproval && hasBothAllowDecisions && !argumentEditDirty"
        v-model="rememberForSession"
        color="primary"
        label="Für diese Session merken"
        :disable="Boolean(loadingDecision) || matchingDraftDirty"
      />
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
        :label="isMemoryRemove ? 'Entfernen genehmigen' : memoryEdit ? 'Speichern und genehmigen' : 'Genehmigen'"
        :loading="loadingDecision === 'approve'"
        :disable="Boolean(loadingDecision) || invalidMemoryApproval"
        @click="decide('approve')"
      />
      <q-btn
        v-if="hasDecision('allow_once') && !hasBothAllowDecisions"
        outline
        no-caps
        color="primary"
        label="Für diesen Lauf erlauben"
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
        :label="rememberForSession && !argumentEditDirty ? 'Für Session erlauben' : 'Für diesen Lauf erlauben'"
        :loading="loadingDecision === allowDecision"
        :disable="Boolean(loadingDecision) || matchingDraftDirty"
        @click="decide(allowDecision)"
      />
    </q-card-actions>
  </q-card>
</template>

<script setup>
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { computed, ref } from 'vue'
import ToolPresentation from 'src/features/chat/components/ToolPresentation.vue'
import SubagentRequestLink from 'src/features/subagents/SubagentRequestLink.vue'
import { useConfirm } from 'src/composables/confirm'
import {
  approvalResource,
  approvalActionDisplay,
  approvalGrantOptions,
  memoryApprovalDisplay
} from 'src/utils/approval-display'
import {
  approvalPresentationKind as resolveApprovalPresentationKind,
  toolPresentation
} from 'src/utils/tool-display'
import { useApprovalStore } from 'stores/approvals'
import { useAuthStore } from 'stores/auth'
import { useChatStore } from 'src/features/chat/store'
import { useWorkspaceStore } from 'stores/workspace'
import { useSkillStore } from 'stores/skills'

defineOptions({ name: 'ApprovalCard' })

const props = defineProps({
  approval: { type: Object, required: true },
  showDraftAction: { type: Boolean, default: true }
})
const emit = defineEmits(['open-approval-draft'])
const approvals = useApprovalStore()
const auth = useAuthStore()
const chat = useChatStore()
const workspace = useWorkspaceStore()
const skills = useSkillStore()
const reviewingSkill = ref(false)
const { confirm } = useConfirm()
const loadingDecision = ref(null)
const memoryEdit = ref(null)
const memoryFormRef = ref(null)
const argumentEdit = ref(null)
const argumentFormRef = ref(null)
const memoryCategoryOptions = ['context', 'fact', 'goal', 'identity', 'preference']
  .map((value) => ({ value, label: memoryCategoryLabel(value) }))
const memoryAudienceOptions = ['private_only', 'public_only', 'both']
  .map((value) => ({ value, label: memoryAudienceLabel(value) }))
const rememberForSession = ref(false)
const grantOption = ref('requested')
const grantOptions = computed(() => approvalGrantOptions(props.approval))
const actionDisplay = computed(() => approvalActionDisplay(props.approval))
const grantDisplay = computed(() => {
  if (argumentEditDirty.value || props.approval.payload?.runActionOnly) return { label: 'diese Aktion mit den angezeigten Werten' }
  return grantOptions.value.find((option) => option.id === grantOption.value) || {
    label: (props.approval.details?.displayItems || props.approval.payload?.displayItems || [])
      .find((item) => ['Freigegebener Bereich', 'Für diese Session'].includes(item.label))?.value?.join(' · ') || ''
  }
})
const argumentEditPayload = computed(() => props.approval.payload?.argumentEdit)
const canEditArguments = computed(() => (
  props.approval.status === 'pending'
  && hasDecision('allow_once')
  && Array.isArray(argumentEditPayload.value?.fields)
  && argumentEditPayload.value.fields.length > 0
))
const argumentEditDirty = computed(() => argumentEdit.value?.some((field) => (
  field.value !== field.initialValue
)) || false)
const isMemoryApproval = computed(() => props.approval.requestType === 'memory_change')
const memoryDisplay = computed(() => memoryApprovalDisplay(props.approval))
const memoryPayload = computed(() => memoryDisplay.value.payload)
const memoryTargets = computed(() => memoryDisplay.value.targets)
const memoryPinned = computed(() => memoryTargets.value.some((memory) => memory.pinned))
const canEditMemory = computed(() => isMemoryApproval.value
  && props.approval.status === 'pending'
  && ['memory_update', 'memory_merge'].includes(props.approval.actionKind)
  && !invalidMemoryApproval.value && hasDecision('approve'))
const invalidMemoryApproval = computed(() => isMemoryApproval.value && memoryDisplay.value.invalid)
const isMemoryRemove = computed(() => isMemoryApproval.value && memoryDisplay.value.destructive)
const permissionsOnly = computed(() => hasDecision('allow_session') && !hasDecision('allow_once'))
const cardTitle = computed(() => isMemoryApproval.value ? memoryDisplay.value.title
  : permissionsOnly.value ? props.approval.actionKind === 'shell_run' ? 'Shell-Zugriff freigeben' : 'Zugriff freigeben'
  : props.approval.actionKind === 'shell_run' ? 'Shell-Befehl ausführen' : props.approval.title)
const memoryTargetLabel = computed(() => memoryDisplay.value.targetLabel)
const actionIcon = computed(() => {
  if (isMemoryApproval.value) return memoryDisplay.value.icon
  if (props.approval.capability?.startsWith('memory.')) return 'psychology'
  if (props.approval.capability?.startsWith('filesystem') || props.approval.capability?.startsWith('session.files.')) return 'folder'
  if (props.approval.capability === 'execution.directory') return 'drive_file_move'
  if (props.approval.capability === 'system.info') return 'computer'
  if (props.approval.capability?.startsWith('shell.') || props.approval.capability === 'process.execute') return 'terminal'
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
const resourceInGrant = computed(() => !argumentEditDirty.value
  && (hasDecision('allow_once') || hasDecision('allow_session'))
  && grantDisplay.value.label === resource.value.value)
const skillDraftApproval = computed(() => (
  props.approval.actionKind === 'build_skill' || props.approval.actionKind === 'edit_skill'
))
const approvalTool = computed(() => ({
  name: props.approval.details?.tool || props.approval.payload?.tool,
  arguments: props.approval.details?.arguments || props.approval.payload?.arguments
}))
const approvalPresentationKind = computed(() => resolveApprovalPresentationKind(approvalTool.value))
const displayItems = computed(() => (
  props.approval.details?.displayItems || props.approval.payload?.displayItems || []
).filter((item) => !['Freigegebener Bereich', 'Für diese Session'].includes(item.label)))
const computer = computed(() => displayItems.value.find((item) => item.label === 'Computer')?.value)
const presentation = computed(() => {
  const result = toolPresentation(approvalTool.value, displayItems.value)
  const resourceLabels = [resource.value.label, 'Pfad', 'Datei', 'Ordner', 'Adresse', 'Arbeitsverzeichnis', 'Terminal-Ordner']
  const fileAccess = ['filesystem.write', 'session.files.write'].includes(props.approval.capability)
    && hasDecision('allow_session') && !props.approval.payload?.runActionOnly
  const fileContentLabels = ['Änderungen', 'Vorher', 'Nachher', 'Dateiinhalt', 'Inhalt', 'Text', 'Patch']
  return {
    ...result,
    items: result.items.filter((item) => item.label !== 'Computer'
      && !(fileAccess && fileContentLabels.includes(item.label))
      && !(resourceLabels.includes(item.label) && item.value === resource.value.value))
  }
})
const skillPreview = computed(() => skillDraftApproval.value
  ? skills.draftReviews[props.approval.id] || props.approval.details?.skillPreview
  : null)
const draftFile = computed(() => skillDraftApproval.value
  ? props.approval.details?.draftFile
  : null)
const childSkillDraft = computed(() => skillDraftApproval.value && Boolean(props.approval.payload?.subagentSessionId))
const matchingDraftDirty = computed(() => Boolean(
  !childSkillDraft.value && draftFile.value
  && workspace.editorDirty
  && workspace.sessionId === props.approval.sessionId
  && workspace.scope === 'session'
  && workspace.viewedEntry?.path?.startsWith(draftFile.value.path.slice(0, -'SKILL.md'.length))
))
const draftRevision = computed(() => draftFile.value
  ? skills.draftReviews[props.approval.id]?.revision ?? draftFile.value.initialRevision
  : undefined)
const hasBothAllowDecisions = computed(() => (
  hasDecision('allow_once') && hasDecision('allow_session')
))
const allowDecision = computed(() => (
  argumentEditDirty.value ? 'allow_once' : rememberForSession.value ? 'allow_session' : 'allow_once'
))

function hasDecision(decision) {
  return Array.isArray(props.approval.availableDecisions)
    && props.approval.availableDecisions.includes(decision)
}

async function reviewSkill() {
  reviewingSkill.value = true
  try { await skills.reviewDraft(props.approval.id) } catch { /* Central API toast. */ }
  finally { reviewingSkill.value = false }
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

function startMemoryEdit() {
  memoryEdit.value = { ...memoryPayload.value.after, pinned: memoryPinned.value }
}

function inputValue(field) {
  if (field.inputType === 'datetime-local') return field.value.replace(/(?:Z|[+-]\d{2}:\d{2})$/, '').slice(0, 16)
  return field.value
}

function submittedValue(field) {
  if (field.value === field.initialValue) return field.originalValue
  if (field.inputType !== 'datetime-local') return field.value
  const offset = field.originalValue.match(/(?:Z|[+-]\d{2}:\d{2})$/)?.[0]
  return offset ? `${field.value}${offset}` : field.value
}

function startArgumentEdit() {
  argumentEdit.value = argumentEditPayload.value.fields.map((field) => {
    const value = inputValue(field)
    return { ...field, originalValue: field.value, initialValue: value, value }
  })
  rememberForSession.value = false
}

function argumentRules(field) {
  return [
    (value) => !field.required || Boolean(value?.trim()) || `Bitte ${field.label.toLowerCase()} eingeben.`,
    (value) => (value?.length ?? 0) <= field.maxLength || `${field.label} darf höchstens ${field.maxLength} Zeichen enthalten.`
  ]
}

async function decide(decision) {
  if (loadingDecision.value) return
  if (decision === 'approve' && memoryEdit.value && !await memoryFormRef.value?.validate()) return
  if (decision.startsWith('allow') && argumentEditDirty.value && !await argumentFormRef.value?.validate()) return
  if (loadingDecision.value) return
  loadingDecision.value = decision
  try {
    const edit = decision === 'approve' && memoryEdit.value
      ? { ...memoryEdit.value, content: memoryEdit.value.content.trim() }
      : undefined
    if (edit) {
      if (memoryPayload.value.after.audience === 'private_only' && edit.audience !== 'private_only') {
        const accepted = await confirm(
          'Die Erinnerung wird danach auch für öffentliche Modelle verfügbar. Bereits übermittelte Inhalte lassen sich nicht zurückholen. Wirklich freigeben?',
          'Für öffentliche Modelle freigeben',
          { okLabel: 'Freigeben', okColor: 'warning' }
        )
        if (!accepted) return
      }
      if (edit.pinned && !memoryPinned.value) {
        const accepted = await confirm(
          'Diese Erinnerung wird bei erlaubten Anfragen vollständig in den Modellkontext eingefügt und vergrößert ihn.',
          'Immer im Kontext',
          { okLabel: 'Bestätigen', okColor: 'primary' }
        )
        if (!accepted) return
      }
    }
    const toolEdit = decision === 'allow_once' && argumentEditDirty.value
      ? {
          revision: argumentEditPayload.value.revision,
          edits: argumentEdit.value.map((field) => ({
            path: field.path,
            value: submittedValue(field)
          }))
        }
      : undefined
    const selectedGrant = decision.startsWith('allow') && !argumentEditDirty.value && grantOptions.value.length > 1
      ? grantOption.value : undefined
    await approvals.decide(props.approval.id, decision, draftRevision.value, edit, toolEdit, selectedGrant)
  } catch {
    // The central API interceptor displays the error; local edits remain available.
  } finally {
    loadingDecision.value = null
  }
}
</script>
