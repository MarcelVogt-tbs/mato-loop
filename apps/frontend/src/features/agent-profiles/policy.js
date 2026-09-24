export function policyOverride(root, path) {
  let current = root
  for (const key of path) {
    current = current?.[key]
    if (current == null) return 'inherit'
  }
  return current
}

export function setPolicyOverride(root, path, policy) {
  if (policy !== 'inherit') {
    let current = root
    for (const key of path.slice(0, -1)) current = current[key] ||= {}
    current[path.at(-1)] = policy
    return
  }

  const parents = []
  let current = root
  for (const key of path.slice(0, -1)) {
    if (!current[key]) return
    parents.push([current, key])
    current = current[key]
  }
  delete current[path.at(-1)]
  for (const [parent, key] of parents.reverse()) {
    if (Object.keys(parent[key]).length) break
    delete parent[key]
  }
}
