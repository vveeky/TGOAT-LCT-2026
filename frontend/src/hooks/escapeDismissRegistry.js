export function createEscapeDismissRegistry() {
  const handlers = []

  return {
    register(handler) {
      const entry = { token: Symbol('escape-dismiss'), handler }
      handlers.push(entry)
      return () => {
        const index = handlers.findIndex((candidate) => candidate.token === entry.token)
        if (index !== -1) handlers.splice(index, 1)
      }
    },
    dismissTop() {
      const top = handlers.at(-1)
      if (!top) return false
      top.handler()
      return true
    },
  }
}
