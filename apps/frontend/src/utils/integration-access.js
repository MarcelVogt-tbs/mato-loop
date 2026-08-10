export function applicableResourceActions(actions, resource) {
  return actions.filter((action) => resource.applicableCapabilities.includes(action.capability))
}

export function calendarResources(resources) {
  return resources.filter((resource) => resource.key.startsWith('calendar:'))
}

export function taskListResources(resources) {
  return resources.filter((resource) => resource.key.startsWith('tasklist:'))
}

export function groupResources(resources) {
  const result = []
  const groups = new Map()
  for (const resource of resources) {
    if (resource.groupKey == null) {
      result.push([resource])
      continue
    }
    if (!groups.has(resource.groupKey)) {
      groups.set(resource.groupKey, [])
      result.push(groups.get(resource.groupKey))
    }
    groups.get(resource.groupKey).push(resource)
  }
  return result
}
