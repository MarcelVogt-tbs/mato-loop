<template>
  <q-dialog
    :transition-duration="0"
    :model-value="Boolean(preview)"
    maximized
    aria-labelledby="approval-draft-preview-title"
    @update:model-value="clear"
  >
    <q-card v-if="preview" class="column no-wrap full-height">
      <q-card-section class="row items-center no-wrap q-gutter-sm">
        <div class="col overflow-hidden">
          <h2 id="approval-draft-preview-title" class="text-subtitle1 text-weight-bold q-my-none ellipsis">{{ preview.filename }}</h2>
          <div class="text-caption app-muted">Skill-Entwurf des Subagenten · Nur Lesezugriff</div>
        </div>
        <q-btn flat round icon="close" aria-label="Entwurfsvorschau schließen" @click="clear" />
      </q-card-section>
      <q-separator />
      <q-scroll-area class="col">
        <div v-if="preview.loading" class="row justify-center q-pa-lg" role="status">
          <q-spinner color="primary" size="32px" />
          <span class="app-sr-only">Entwurf wird geladen</span>
        </div>
        <q-banner v-else-if="preview.error" class="app-surface-tonal q-ma-md" role="alert">
          {{ apiErrorMessage(preview.error) }}
        </q-banner>
        <pre v-else class="file-viewer__text q-pa-md">{{ preview.content }}</pre>
      </q-scroll-area>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useApprovalStore } from 'stores/approvals'
import { useControlCenterStore } from 'stores/control-center'
import { useWorkspaceStore } from 'stores/workspace'
import { useChatStore } from 'src/features/chat/store'
import { apiErrorMessage } from 'src/utils/api-errors'

const route = useRoute()
const chat = useChatStore()
const approvals = useApprovalStore()
const controlCenter = useControlCenterStore()
const workspace = useWorkspaceStore()
const preview = ref(null)
let operation, disposed = false
function clear() { operation = null; preview.value = null }

async function open(approval) {
  const draft = approval?.details?.draftFile
  const sessionId = approval?.sessionId
  if (disposed || !['build_skill', 'edit_skill'].includes(approval?.actionKind)
    || !draft || chat.current?.id !== sessionId) return
  clear()
  const request = {}
  operation = request
  const current = () => !disposed && operation === request && chat.current?.id === sessionId
  if (approval.payload?.subagentSessionId) {
    const revision = draft.fileRevision
    preview.value = { filename: draft.filename, loading: true, content: '', error: null }
    const state = preview.value
    try {
      const data = await approvals.previewSkillDraft(approval)
      if (!current()) return
      if (data?.kind !== 'text' || typeof data.content !== 'string' || !revision) {
        state.error = { code: 'skill.draftInvalid' }
      } else if (data.revision !== revision) {
        state.error = { code: 'file.changed' }
      } else {
        state.content = data.content
      }
    } catch (error) {
      if (current()) state.error = error
    } finally {
      if (current()) state.loading = false
    }
    return
  }
  const tabId = await controlCenter.openFile({
    scope: 'session', path: draft.path, name: draft.filename,
    rootIdentity: draft.rootIdentity || sessionId, readOnly: false
  })
  if (current() && tabId) workspace.startFileTabEdit(tabId)
}

watch(() => route.fullPath, clear, { flush: 'sync' })
onBeforeUnmount(() => { disposed = true; clear() })
defineExpose({ open })
</script>
