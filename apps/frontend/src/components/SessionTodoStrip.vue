<template>
  <div v-if="visibleTodos.length" class="session-todos" aria-live="polite">
    <div class="session-todos__list" role="list" aria-label="Aufgaben dieser Session">
      <div v-for="(todo, index) in visibleTodos" :key="index" class="session-todos__item" role="listitem">
        <q-icon
          :name="statusIcon[todo.status]"
          :color="todo.status === 'completed' ? 'positive' : todo.status === 'in_progress' ? 'primary' : undefined"
          size="18px"
          aria-hidden="true"
        />
        <span class="session-todos__content" :class="{ 'text-strike app-muted': todo.status === 'cancelled' }">{{ todo.content }}</span>
        <span v-if="!expanded" class="session-todos__position app-muted">· {{ currentPosition }}/{{ todos.length }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { QIcon } from 'quasar'
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
const visibleTodos = computed(() => {
  if (props.expanded) return props.todos
  if (!props.running) return []
  const current = props.todos.find((todo) => todo.status === 'in_progress')
    || props.todos.find((todo) => todo.status === 'pending')
  return current ? [current] : []
})
const currentPosition = computed(() => props.todos.indexOf(visibleTodos.value[0]) + 1)
</script>

<style scoped>
.session-todos {
  min-width: 0;
}

.session-todos__list {
  display: flex;
  min-width: 0;
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

.session-todos__position {
  flex: none;
}
</style>
