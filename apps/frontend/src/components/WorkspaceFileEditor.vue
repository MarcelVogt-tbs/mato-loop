<template>
  <div class="workspace-editor column no-wrap" @keydown="handleKeydown">
    <div class="workspace-editor__actions row items-center q-pa-sm q-gutter-sm">
      <q-btn-toggle
        v-if="markdown"
        :model-value="mode"
        no-caps
        dense
        unelevated
        toggle-color="primary"
        :options="modeOptions"
        @update:model-value="setMode"
      />
      <q-space />
      <span class="workspace-editor__status" aria-live="polite" aria-atomic="true">
        <q-badge v-if="dirty" color="warning" text-color="black" label="Ungespeichert" />
      </span>
      <q-btn flat no-caps label="Abbrechen" :disable="saving" @click="emit('cancel')" />
      <q-btn
        unelevated
        no-caps
        color="primary"
        icon="save"
        label="Speichern"
        :loading="saving"
        :disable="!dirty"
        @click="emit('save')"
      />
    </div>
    <div v-if="markdown && !visualAvailable" class="text-caption app-muted q-px-sm q-pb-sm">
      Markdown mit Bildverweisen bleibt zum Schutz vor externen Abrufen im Quelltextmodus.
    </div>
    <q-separator />

    <div v-if="markdown && mode === 'visual'" class="workspace-editor__visual col">
      <div v-if="loading" class="fit flex flex-center"><q-spinner color="primary" size="32px" /></div>
      <div v-show="!loading" ref="toastRoot" class="workspace-editor__toast fit" />
    </div>
    <q-input
      v-else
      ref="sourceEditor"
      class="workspace-editor__textarea col"
      input-class="workspace-editor__textarea-input"
      type="textarea"
      outlined
      square
      :aria-label="markdown ? 'Markdown-Quelltext' : 'Textinhalt'"
      :model-value="modelValue"
      @update:model-value="emit('update:modelValue', $event)"
    />
  </div>
</template>

<script setup>
import DOMPurify from 'dompurify'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { containsMarkdownImage } from 'src/utils/markdown'

defineOptions({ name: 'WorkspaceFileEditor' })
const props = defineProps({
  modelValue: { type: String, default: '' },
  markdown: Boolean,
  saving: Boolean,
  dirty: Boolean
})
const emit = defineEmits(['update:modelValue', 'save', 'cancel'])
const $q = useQuasar()
const toastRoot = ref(null)
const sourceEditor = ref(null)
const visualAvailable = computed(() => props.markdown && !containsMarkdownImage(props.modelValue))
const mode = ref(visualAvailable.value ? 'visual' : 'source')
const loading = ref(false)
const modeOptions = computed(() => [
  { label: 'Visuell', value: 'visual', disable: !visualAvailable.value },
  { label: 'Markdown', value: 'source' }
])
let editor = null
let editorModule = null
let mountVersion = 0

async function mountToastEditor() {
  if (!visualAvailable.value || mode.value !== 'visual') return
  const version = ++mountVersion
  loading.value = true
  try {
    if (!editorModule) {
      const [module] = await Promise.all([
        import('@toast-ui/editor'),
        import('@toast-ui/editor/dist/i18n/de-de')
      ])
      editorModule = module.default
    }
    await nextTick()
    if (version !== mountVersion || !toastRoot.value) return
    editor?.destroy()
    editor = new editorModule({
      el: toastRoot.value,
      height: '100%',
      initialEditType: 'wysiwyg',
      initialValue: props.modelValue,
      language: 'de-DE',
      ...($q.dark.isActive ? { theme: 'dark' } : {}),
      usageStatistics: false,
      hideModeSwitch: true,
      toolbarItems: [
        ['heading', 'bold', 'italic', 'strike'],
        ['hr', 'quote'],
        ['ul', 'ol', 'task', 'indent', 'outdent'],
        ['table', 'link'],
        ['code', 'codeblock']
      ],
      customHTMLSanitizer: (html) => DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        FORBID_TAGS: ['audio', 'embed', 'iframe', 'img', 'link', 'object', 'source', 'video'],
        FORBID_ATTR: ['poster', 'src', 'srcset', 'style']
      }),
      hooks: { addImageBlobHook: () => false }
    })
    editor.on('change', () => emit('update:modelValue', editor.getMarkdown()))
    editor.focus()
  } catch {
    if (version === mountVersion) {
      mode.value = 'source'
      $q.notify({ type: 'negative', message: 'Der visuelle Markdown-Editor konnte nicht geladen werden.' })
    }
  } finally {
    if (version === mountVersion) loading.value = false
  }
}

async function setMode(value) {
  if (value === mode.value || (value === 'visual' && !visualAvailable.value)) return
  if (value === 'source' && editor) {
    emit('update:modelValue', editor.getMarkdown())
    editor.destroy()
    editor = null
  }
  mode.value = value
  if (value === 'visual') await mountToastEditor()
  else await nextTick(() => sourceEditor.value?.focus())
}

function handleKeydown(event) {
  if (event.key.toLocaleLowerCase() !== 's' || (!event.ctrlKey && !event.metaKey) || event.altKey) return
  event.preventDefault()
  if (props.dirty && !props.saving) emit('save')
}

async function focusEditor() {
  if (mode.value === 'visual') await mountToastEditor()
  else await nextTick(() => sourceEditor.value?.focus())
}

watch(() => $q.dark.isActive, () => {
  if (mode.value === 'visual') mountToastEditor()
})
watch(visualAvailable, (available) => {
  if (!available && mode.value === 'visual') setMode('source')
})

onMounted(focusEditor)
onBeforeUnmount(() => {
  mountVersion += 1
  editor?.destroy()
})
</script>

<style src="@toast-ui/editor/dist/toastui-editor.css"></style>
<style src="@toast-ui/editor/dist/theme/toastui-editor-dark.css"></style>
