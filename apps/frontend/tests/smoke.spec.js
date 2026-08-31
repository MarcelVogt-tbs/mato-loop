import { describe, expect, it } from 'vitest'

import routes, { settingsRoutes } from '../src/router/routes'

describe('MatoLoop frontend scaffold', () => {
  it('exposes chat and the complete settings navigation', () => {
    const appRoute = routes.find((route) => Array.isArray(route.children))
    const routeNames = appRoute.children.map((route) => route.name)
    const settingsRoute = appRoute.children.find((route) => route.name === 'settings')

    expect(routeNames).toEqual(['chat', 'settings'])
    expect(settingsRoute.children.map((route) => route.name)).toEqual([
      'settings-providers',
      'settings-default-models',
      'settings-agents',
      'settings-integrations',
      'settings-tools',
      'settings-approvals',
      'settings-execution-agents',
      'settings-skills',
      'settings-automations',
      'settings-personalization',
      'settings-persona',
      'settings-memory',
      'settings-users',
      'settings-instance-limits'
    ])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-integrations').children
        .map((route) => route.name)
    ).toEqual(['settings-integration-access'])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-agents').children
        .map((route) => route.name)
    ).toEqual(['settings-agent-create', 'settings-agent-edit'])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-automations').children
        .map(({ name, path }) => ({ name, path }))
    ).toEqual([
      { name: 'settings-automation-create', path: 'new' },
      { name: 'settings-automation-edit', path: ':automationId/edit' },
      { name: 'settings-automation-detail', path: ':automationId' }
    ])
    expect(routes.find((route) => route.name === 'markdown-print')?.path).toBe('/print/markdown')
    expect(settingsRoutes.find((route) => route.name === 'settings-users')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.find((route) => route.name === 'settings-instance-limits')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.some((route) => route.name === 'settings-account')).toBe(false)
    expect(settingsRoutes.find((route) => route.name === 'settings-approvals')?.redirect).toEqual({
      name: 'chat', query: { controlCenter: 'approvals' }
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-personalization')?.meta).toEqual({
      settingsLabel: 'Personalisierung', settingsIcon: 'manage_accounts'
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-persona')?.redirect).toEqual({
      name: 'settings-personalization', query: { tab: 'assistant' }
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-persona')?.meta).toBeUndefined()
    expect(settingsRoutes
      .filter((route) => ['settings-providers', 'settings-default-models', 'settings-integrations', 'settings-tools'].includes(route.name))
      .every((route) => route.meta.requiresAdmin === undefined)).toBe(true)
  })
})
