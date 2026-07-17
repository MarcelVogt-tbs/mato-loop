import { describe, expect, it } from 'vitest'

import routes from '../src/router/routes'

describe('MatoLoop frontend scaffold', () => {
  it('exposes the initial chat and settings routes', () => {
    const routeNames = routes[0].children.map((route) => route.name)

    expect(routeNames).toEqual(['chat', 'settings'])
  })
})
