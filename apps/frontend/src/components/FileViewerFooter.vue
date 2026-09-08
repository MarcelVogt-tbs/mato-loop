<template>
  <div
    v-if="filePath"
    class="q-px-md q-py-xs text-caption app-muted"
  >
    <div class="row items-center no-wrap" tabindex="0" :aria-label="`Dateipfad: ${filePath}`">
      <q-icon name="folder_open" size="18px" class="q-mr-xs" />
      <span class="ellipsis">{{ filePath }}</span>
      <q-tooltip>{{ filePath }}</q-tooltip>
    </div>
    <div v-if="status" class="row items-center q-gutter-x-md">
      <span>Zeile {{ status.line }}, Spalte {{ status.column }}</span>
      <span>{{ status.lines.toLocaleString('de-DE') }} Zeilen</span>
      <span>{{ status.bytes.toLocaleString('de-DE') }} Byte (UTF-8)</span>
      <span>{{ status.language }}</span>
      <span role="status">{{ workspace.editorDirty ? 'Ungespeichert' : 'Gespeichert' }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useControlCenterStore } from 'stores/control-center'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileViewerFooter' })

const controlCenter = useControlCenterStore()
const workspace = useWorkspaceStore()
const status = computed(() => workspace.editorActive && workspace.activeFileTabId === controlCenter.activeTab?.tabId
  ? workspace.editorSession : null)
const filePath = computed(() => (
  controlCenter.activeTab?.kind === 'file' ? controlCenter.activeTab.fileTarget.path : ''
))
</script>
