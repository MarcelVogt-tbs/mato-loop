<template>
  <q-card v-if="visibleTodos.length" flat bordered class="session-todos app-surface" aria-live="polite">
    <div class="session-todos__header text-weight-medium app-muted">Aufgaben · {{ progressPosition }}/{{ todos.length }}</div>
    <q-separator />
    <div class="session-todos__list" role="list" aria-label="Aufgaben dieser Session">
      <div v-for="(todo, index) in visibleTodos" :key="index" class="session-todos__item" role="listitem">
        <q-icon
          :name="statusIcon[todo.status]"
          :color="todo.status === 'completed' ? 'positive' : todo.status === 'in_progress' ? 'primary' : undefined"
          size="18px"
          aria-hidden="true"
        />
        <span class="session-todos__content" :class="{ 'text-strike app-muted': todo.status === 'cancelled' }">{{ todo.content }}</span>
      </div>
    </div>
  </q-card>
</template>

<script setup>
import { QCard, QIcon, QSeparator } from 'quasar'
import { computed } from 'vue'

defineOptions({ name: 'SessionTodoStrip' })

const props = defineProps({
  todos: { type: Array, default: () => [] },
  expanded: Boolean,
  running: Boolean
})
const statusIcon = {
  pending: 'radio_button_unchecked',
  in_progress: 'pending',
  completed: 'check_circle',
  cancelled: 'remove_circle_outline'
}
const currentIndex = computed(() => {
  const inProgress = props.todos.findIndex((todo) => todo.status === 'in_progress')
  return inProgress >= 0 ? inProgress : props.todos.findIndex((todo) => todo.status === 'pending')
})
const visibleTodos = computed(() => {
  if (props.expanded) return props.todos
  if (!props.running) return []
  return currentIndex.value >= 0 ? [props.todos[currentIndex.value]] : []
})
const progressPosition = computed(() => currentIndex.value >= 0 ? currentIndex.value + 1 : props.todos.length)
</script>

<style scoped>
.session-todos {
  min-width: 0;
}

.session-todos__header {
  min-height: 24px;
  padding: 3px 8px;
  font-size: 0.7rem;
  line-height: 18px;
}

.session-todos__list {
  display: flex;
  min-width: 0;
  padding: 4px;
  flex-direction: column;
}

.session-todos__item {
  display: flex;
  min-width: 0;
  min-height: 24px;
  padding: 0 4px;
  align-items: center;
  gap: 8px;
  font-size: 0.75rem;
  line-height: 20px;
}

.session-todos__content {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
