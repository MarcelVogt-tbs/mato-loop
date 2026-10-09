<template>
  <div v-if="actions.length" class="work-history__file-outputs">
    <div
      v-for="action in actions"
      :key="`${action.rootIdentity || action.executionAgentId}:${action.path}`"
      class="work-history__file-output row no-wrap items-center text-caption"
    >
      <q-icon :name="['directory', 'collection'].includes(action.kind) ? 'folder_zip' : attachmentIcon(action)" class="app-muted" size="18px" />
      <span class="col ellipsis" :title="action.path">{{ action.kind === 'directory' ? `${action.filename}.zip` : action.filename }}</span>
      <q-btn
        v-if="!readOnly && action.kind === 'file'"
        flat round dense size="sm" icon="visibility"
        class="work-history__file-action col-auto app-muted"
        :aria-label="`${action.filename} ansehen`"
        @click="emit('open-file', action)"
      >
        <q-tooltip>Datei ansehen</q-tooltip>
      </q-btn>
      <q-btn
        flat round dense size="sm" icon="download"
        class="work-history__file-action col-auto app-muted"
        :href="action.downloadUrl"
        :download="action.kind === 'directory' ? `${action.filename}.zip` : action.filename"
        :aria-label="`${action.filename} herunterladen`"
      >
        <q-tooltip>Datei herunterladen</q-tooltip>
      </q-btn>
    </div>
  </div>
</template>

<script setup>
import { attachmentIcon } from 'src/utils/attachments'

defineProps({
  actions: { type: Array, default: () => [] },
  readOnly: Boolean
})
const emit = defineEmits(['open-file'])
</script>
