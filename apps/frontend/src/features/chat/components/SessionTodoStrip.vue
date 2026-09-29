<template>
  <q-card v-if="todos.length" flat class="session-todos app-surface overflow-hidden">
    <q-btn
      flat
      no-caps
      align="between"
      class="full-width text-caption app-muted"
      :label="`Aufgaben · ${completedCount} von ${todos.length} erledigt`"
      :icon-right="collapsed ? 'keyboard_arrow_up' : 'keyboard_arrow_down'"
      :aria-expanded="!collapsed"
      :aria-controls="listId"
      :disable="saving"
      @click="$emit('update:collapsed', !collapsed)"
    />
    <q-linear-progress
      :value="completedCount / todos.length"
      :color="completedCount === todos.length ? 'positive' : 'primary'"
      size="2px"
      aria-label="Aufgabenfortschritt"
    />
    <div v-if="collapsed && activeTodo" class="session-todos__item row no-wrap items-center q-ma-xs q-px-xs" role="status">
      <q-icon :name="statusIcon.in_progress" color="primary" size="18px" class="col-auto" aria-hidden="true" />
      <span class="app-sr-only">In Bearbeitung: </span>
      <span class="col ellipsis" :title="activeTodo.content">{{ activeTodo.content }}</span>
    </div>
    <div v-show="!collapsed" :id="listId" class="session-todos__list column no-wrap scroll q-pa-xs" role="list" aria-label="Aufgaben dieser Session" aria-live="polite">
      <div v-for="(todo, index) in todos" :key="index" class="session-todos__item row no-wrap items-start col-auto q-px-xs" role="listitem">
        <q-icon
          :name="statusIcon[todo.status]"
          :color="todo.status === 'completed' ? 'positive' : todo.status === 'in_progress' ? 'primary' : undefined"
          size="18px"
          role="img"
          aria-hidden="false"
          :aria-label="statusLabel[todo.status]"
          class="col-auto"
        />
        <span class="session-todos__content" :class="{ 'text-strike app-muted': ['completed', 'cancelled'].includes(todo.status) }">{{ todo.content }}</span>
      </div>
    </div>
  </q-card>
</template>

<script setup>
import { QBtn, QCard, QIcon, QLinearProgress } from 'quasar'
import { computed, useId } from 'vue'

defineOptions({ name: 'SessionTodoStrip' })

const props = defineProps({
  todos: { type: Array, default: () => [] },
  collapsed: Boolean,
  saving: Boolean
})
defineEmits(['update:collapsed'])
const listId = useId()
const statusIcon = {
  pending: 'radio_button_unchecked',
  in_progress: 'pending',
  completed: 'check_circle',
  cancelled: 'remove_circle_outline'
}
const statusLabel = {
  pending: 'Offen',
  in_progress: 'In Bearbeitung',
  completed: 'Erledigt',
  cancelled: 'Abgebrochen'
}
const completedCount = computed(() => props.todos.filter((todo) => todo.status === 'completed').length)
const activeTodo = computed(() => props.todos.find((todo) => todo.status === 'in_progress'))
</script>

<style scoped>
.session-todos {
  min-width: 0;
  border-bottom: 0;
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
}

.session-todos__list {
  min-width: 0;
  max-height: min(20dvh, 160px);
}

.session-todos__item {
  min-width: 0;
  min-height: 24px;
  gap: 8px;
  font-size: 0.75rem;
  line-height: 20px;
}

.session-todos__content {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
