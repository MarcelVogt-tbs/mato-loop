<template>
  <q-card flat class="question-dock app-surface" @keydown="handleKeydown">
    <q-card-section class="question-dock__header row items-center no-wrap">
      <div class="row items-center no-wrap q-gutter-sm">
        <q-icon name="help_outline" color="primary" size="20px" />
        <span class="text-subtitle2 text-weight-bold">
          Frage {{ draft.index + 1 }} von {{ request.questions.length }}
        </span>
      </div>
      <div v-if="request.questions.length > 1" class="question-dock__progress row no-wrap">
        <button
          v-for="(item, index) in request.questions"
          :key="item.id"
          type="button"
          class="question-dock__segment"
          :class="{
            'question-dock__segment--active': index === draft.index,
            'question-dock__segment--answered': answered(index)
          }"
          :disabled="busy"
          :aria-label="`Frage ${index + 1}: ${item.header}`"
          :aria-current="index === draft.index ? 'step' : undefined"
          @click="jump(index)"
        >
          <q-tooltip>{{ item.header }}</q-tooltip>
        </button>
      </div>
      <q-space class="question-dock__header-spacer" />
      <q-btn
        flat
        round
        dense
        :icon="draft.minimized ? 'keyboard_arrow_up' : 'keyboard_arrow_down'"
        :aria-label="draft.minimized ? 'Rückfrage ausklappen' : 'Rückfrage einklappen'"
        :disable="busy"
        @click="draft.minimized = !draft.minimized"
      />
      <q-btn
        v-if="showStop"
        flat
        round
        dense
        color="negative"
        icon="stop"
        aria-label="Agent-Anfrage stoppen"
        :loading="stopping"
        :disable="busy"
        @click="emit('stop')"
      >
        <q-tooltip>Agent-Anfrage stoppen</q-tooltip>
      </q-btn>
    </q-card-section>

    <q-separator />

    <q-card-section class="question-dock__body" :class="{ 'question-dock__body--minimized': draft.minimized }">
      <div :id="questionLabelId" ref="questionHeading" class="text-body1 text-weight-bold no-outline" tabindex="-1">
        {{ current.question }}
      </div>

      <template v-if="!draft.minimized">
        <div v-if="current.type === 'multiple_choice'" class="text-caption app-muted q-mt-xs">
          Du kannst mehrere Antworten auswählen.
        </div>
        <div v-else-if="current.type === 'single_choice'" class="text-caption app-muted q-mt-xs">
          Wähle eine Antwort aus.
        </div>

        <div
          v-if="current.type !== 'free_text'"
          ref="optionsElement"
          class="question-dock__options q-mt-sm"
          :role="current.type === 'single_choice' ? 'radiogroup' : 'group'"
          :aria-labelledby="questionLabelId"
        >
          <div
            v-for="option in current.options"
            :key="option.label"
            class="question-dock__option row items-start no-wrap"
          >
            <q-radio
              v-if="current.type === 'single_choice'"
              :model-value="customSelected ? null : (currentAnswers[0] || null)"
              :tabindex="singleTabIndex(option.label)"
              :val="option.label"
              :label="option.label"
              :disable="busy"
              @update:model-value="selectSingle(option.label)"
            />
            <q-checkbox
              v-else
              :model-value="currentAnswers.includes(option.label)"
              :label="option.label"
              :disable="busy"
              @update:model-value="toggleMultiple(option.label)"
            />
            <span v-if="option.description" class="col text-caption app-muted q-pt-sm q-ml-sm">
              {{ option.description }}
            </span>
          </div>

          <div v-if="current.allow_custom !== false" class="question-dock__option">
            <div class="row items-start no-wrap">
              <q-radio
                v-if="current.type === 'single_choice'"
                :model-value="customSelected ? 'custom' : null"
                :tabindex="customSelected ? 0 : -1"
                val="custom"
                label="Eigene Antwort"
                :disable="busy"
                @update:model-value="enableCustom"
              />
              <q-checkbox
                v-else
                :model-value="customSelected"
                label="Eigene Antwort"
                :disable="busy"
                @update:model-value="toggleCustom"
              />
            </div>
            <q-input
              v-if="customSelected"
              :ref="setCustomInput"
              :model-value="customText"
              outlined
              dense
              autogrow
              type="textarea"
              class="q-ml-lg q-mt-xs"
              placeholder="Eigene Antwort eingeben"
              :maxlength="20000"
              :disable="busy"
              @update:model-value="updateCustom"
              @keydown.escape.stop.prevent="closeCustom"
            />
          </div>
        </div>

        <q-input
          v-else
          ref="freeTextInput"
          :model-value="currentAnswers[0] || ''"
          outlined
          autogrow
          type="textarea"
          class="q-mt-md"
          :placeholder="current.placeholder || 'Antwort eingeben'"
          :maxlength="20000"
          :disable="busy"
          :aria-labelledby="questionLabelId"
          @update:model-value="setFreeText"
        />
      </template>
    </q-card-section>

    <q-separator />
    <q-card-actions class="question-dock__footer q-pa-md">
      <q-btn
        flat
        no-caps
        color="negative"
        label="Ablehnen"
        :loading="rejecting"
        :disable="busy"
        @click="reject"
      />
      <q-space class="question-dock__footer-spacer" />
      <q-btn
        v-if="draft.index > 0"
        outline
        no-caps
        color="primary"
        label="Zurück"
        :disable="busy"
        @click="back"
      />
      <q-btn
        unelevated
        no-caps
        color="primary"
        :label="last ? 'Antworten senden' : 'Weiter'"
        :loading="answering"
        :disable="busy"
        @click="next"
      />
    </q-card-actions>
  </q-card>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useQuestionStore } from 'stores/questions'

defineOptions({ name: 'QuestionDock' })

const props = defineProps({
  request: { type: Object, required: true },
  stopping: Boolean,
  showStop: { type: Boolean, default: true }
})
const emit = defineEmits(['stop'])
const questions = useQuestionStore()
const draft = questions.ensureDraft(props.request)
const optionsElement = ref(null)
const customInput = ref(null)
const freeTextInput = ref(null)
const questionHeading = ref(null)
const current = computed(() => props.request.questions[draft.index])
const currentAnswers = computed(() => draft.answers[current.value.id] || [])
const currentOptions = computed(() => new Set((current.value.options || []).map((option) => option.label)))
const customText = computed(() => draft.custom[current.value.id] || '')
const customSelected = computed(() => draft.customOn[current.value.id] === true)
const last = computed(() => draft.index === props.request.questions.length - 1)
const rejecting = ref(false)
const ownSubmission = computed(() => questions.submittingId === props.request.id)
const busy = computed(() => questions.submittingId !== null || props.stopping)
const answering = computed(() => ownSubmission.value && !rejecting.value)
const questionLabelId = computed(() => `question-${props.request.id}-${current.value.id}`)

function answered(index) {
  return Boolean(draft.answers[props.request.questions[index].id]?.some((answer) => answer.trim()))
}

function singleTabIndex(label) {
  if (customSelected.value) return -1
  return (currentAnswers.value[0] || current.value.options[0]?.label) === label ? 0 : -1
}

function jump(index) {
  draft.index = Math.max(0, Math.min(props.request.questions.length - 1, index))
  focusCurrentQuestion()
}

function focusCurrentQuestion() {
  void nextTick(() => (freeTextInput.value || questionHeading.value)?.focus())
}

function back() {
  jump(draft.index - 1)
}

async function next() {
  if (!last.value) {
    jump(draft.index + 1)
    return
  }
  try {
    await questions.answer(props.request.id, draft.answers)
  } catch {
    // Axios already displayed the API error.
  }
}

async function reject() {
  rejecting.value = true
  try {
    await questions.reject(props.request.id)
  } catch {
    // Axios already displayed the API error.
  } finally {
    rejecting.value = false
  }
}

function selectSingle(label) {
  draft.customOn[current.value.id] = false
  draft.answers[current.value.id] = [label]
}

function toggleMultiple(label) {
  const answers = currentAnswers.value
  draft.answers[current.value.id] = answers.includes(label)
    ? answers.filter((answer) => answer !== label)
    : [...answers, label]
}

function enableCustom() {
  draft.customOn[current.value.id] = true
  updateCustom(customText.value)
  void nextTick(() => customInput.value?.focus?.())
}

function toggleCustom(enabled) {
  draft.customOn[current.value.id] = enabled
  updateCustom(enabled ? customText.value : '')
  if (enabled) void nextTick(() => customInput.value?.focus?.())
}

function updateCustom(value) {
  const nextValue = String(value || '')
  const cleaned = nextValue.trim()
  draft.custom[current.value.id] = nextValue
  const listed = currentAnswers.value.filter((answer) => currentOptions.value.has(answer))
  if (current.value.type === 'single_choice') {
    draft.answers[current.value.id] = cleaned ? [nextValue] : []
    return
  }
  draft.answers[current.value.id] = cleaned && !listed.includes(cleaned)
    ? [...listed, nextValue]
    : listed
}

function closeCustom() {
  if (customText.value.trim()) {
    focusOptions()
    return
  }
  toggleCustom(false)
}

function setFreeText(value) {
  draft.answers[current.value.id] = String(value || '').trim() ? [String(value)] : []
}

function setCustomInput(element) {
  customInput.value = element
}

function focusOptions() {
  optionsElement.value?.querySelector('[role="radio"], [role="checkbox"]')?.focus()
}

function handleKeydown(event) {
  if (event.defaultPrevented || busy.value) return
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key === 'Enter') {
    event.preventDefault()
    void next()
    return
  }
  const target = event.target
  if (!(target instanceof HTMLElement) || !target.matches('[role="radio"], [role="checkbox"]')) return
  if (event.key === 'Enter') {
    event.preventDefault()
    target.click()
    return
  }
  if (target.getAttribute('role') !== 'radio') return
  const keys = ['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const controls = [
    ...(optionsElement.value?.querySelectorAll('[role="radio"]') || [])
  ]
    .filter((control) => control.getAttribute('aria-disabled') !== 'true')
  if (!controls.length) return
  const index = Math.max(0, controls.indexOf(target))
  let nextIndex = 0
  if (event.key === 'End') nextIndex = controls.length - 1
  else if (event.key !== 'Home') {
    const step = ['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1
    nextIndex = (index + step + controls.length) % controls.length
  }
  event.preventDefault()
  controls[nextIndex]?.focus()
  controls[nextIndex]?.click()
}

onMounted(focusCurrentQuestion)
</script>
