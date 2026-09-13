<template>
  <q-card
    v-if="todos.length"
    flat
    bordered
    class="session-todos app-surface"
    aria-live="polite"
  >
    <q-item dense>
      <q-item-section avatar>
        <q-icon name="checklist" color="primary" />
      </q-item-section>
      <q-item-section>
        <q-item-label class="text-weight-medium">{{ completedCount }} von {{ todos.length }} Aufgaben erledigt</q-item-label>
      </q-item-section>
    </q-item>
    <template v-if="expanded">
      <q-separator />
      <q-list dense class="session-todos__list" aria-label="Aufgaben dieser Session">
        <q-item v-for="(todo, index) in todos" :key="index">
          <q-item-section avatar>
            <q-icon
              :name="statusIcon[todo.status]"
              :color="todo.status === 'completed' ? 'positive' : todo.status === 'in_progress' ? 'primary' : undefined"
            />
          </q-item-section>
          <q-item-section :class="{ 'text-strike app-muted': todo.status === 'cancelled' }">
            {{ todo.content }}
          </q-item-section>
        </q-item>
      </q-list>
    </template>
  </q-card>
</template>

<script setup>
import { QCard, QIcon, QItem, QItemLabel, QItemSection, QList, QSeparator } from 'quasar'
import { computed } from 'vue'

defineOptions({ name: 'SessionTodoStrip' })

const props = defineProps({
  todos: { type: Array, default: () => [] },
  expanded: Boolean
})
const statusIcon = {
  pending: 'radio_button_unchecked',
  in_progress: 'pending',
  completed: 'check_circle',
  cancelled: 'remove_circle_outline'
}
const completedCount = computed(() => props.todos.filter((todo) => todo.status === 'completed').length)
</script>

<style scoped>
.session-todos {
  width: min(900px, calc(100% - 32px));
  margin: 8px auto 0;
}

.session-todos__list {
  max-height: 30dvh;
  overflow-y: auto;
}

@media (max-width: 600px) {
  .session-todos {
    width: calc(100% - 16px);
  }
}
</style>
