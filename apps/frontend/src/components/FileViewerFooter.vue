<template>
  <div
    v-if="filePath"
    class="row items-center no-wrap q-px-md q-py-xs text-caption app-muted"
    tabindex="0"
    :aria-label="`Dateipfad: ${filePath}`"
  >
    <q-icon name="folder_open" size="18px" class="q-mr-xs" />
    <span class="ellipsis">{{ filePath }}</span>
    <q-tooltip>{{ filePath }}</q-tooltip>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useControlCenterStore } from 'stores/control-center'

defineOptions({ name: 'FileViewerFooter' })

const controlCenter = useControlCenterStore()
const filePath = computed(() => (
  controlCenter.activeTab?.kind === 'file' ? controlCenter.activeTab.fileTarget.path : ''
))
</script>
