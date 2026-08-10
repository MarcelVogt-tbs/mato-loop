import { describe, expect, it } from 'vitest'

import {
  applicableResourceActions,
  calendarResources,
  groupResources,
  taskListResources
} from '../src/utils/integration-access'

const actions = [
  { capability: 'calendar.read' },
  { capability: 'tasks.read' },
  { capability: 'tasks.write' }
]

describe('integration access display', () => {
  it('limits resource actions to applicable capabilities', () => {
    expect(applicableResourceActions(actions, { applicableCapabilities: ['tasks.read'] }))
      .toEqual([{ capability: 'tasks.read' }])
  })

  it('offers only calendar resources as the default calendar', () => {
    expect(calendarResources([
      { key: 'calendar:work' },
      { key: 'tasklist:work' }
    ])).toEqual([{ key: 'calendar:work' }])
  })

  it('offers only task list resources as the default task list', () => {
    expect(taskListResources([
      { key: 'calendar:work' },
      { key: 'tasklist:work' }
    ])).toEqual([{ key: 'tasklist:work' }])
  })

  it('groups equal explicit keys while leaving missing and null keys standalone', () => {
    const calendar = { key: 'calendar:work', groupKey: 'work' }
    const standalone = { key: 'calendar:private' }
    const tasks = { key: 'tasklist:work', groupKey: 'work' }
    const ungroupedTasks = { key: 'tasklist:private', groupKey: null }

    expect(groupResources([calendar, standalone, tasks, ungroupedTasks])).toEqual([
      [calendar, tasks],
      [standalone],
      [ungroupedTasks]
    ])
  })

  it('preserves first-seen group order without key collisions', () => {
    const first = { key: 'calendar:first', groupKey: '__proto__' }
    const standalone = { key: '__proto__', groupKey: null }
    const second = { key: 'tasklist:second', groupKey: '__proto__' }
    const collidingGroup = { key: 'calendar:third', groupKey: '__proto__:standalone' }

    expect(groupResources([first, standalone, collidingGroup, second])).toEqual([
      [first, second],
      [standalone],
      [collidingGroup]
    ])
  })
})
