/**
 * Deterministic in-memory localStorage stub for vitest.
 *
 * WHY: jsdom 24.1 under Node 26 exposes the `window.localStorage` accessor but
 * the getter returns `undefined` (sessionStorage works; `_localStorage` is
 * never initialized). Persistence tests would TypeError on `.clear()`.
 * Installing a Map-backed Storage on both `window` and `globalThis` gives the
 * suites a real, observable storage contract. Runtime behavior against real
 * browser localStorage is validated by the SA-5 Playwright gate (task 3.3.1).
 *
 * Call in `beforeEach` BEFORE creating the Pinia instance — the run store
 * reads the persisted run_id at store-creation time.
 */

export function installLocalStorageStub(): Storage {
  const data = new Map<string, string>()
  const stub: Storage = {
    getItem: (key: string) => (data.has(key) ? data.get(key)! : null),
    setItem: (key: string, value: string) => {
      data.set(key, String(value))
    },
    removeItem: (key: string) => {
      data.delete(key)
    },
    clear: () => {
      data.clear()
    },
    key: (index: number) => [...data.keys()][index] ?? null,
    get length() {
      return data.size
    },
  }

  Object.defineProperty(window, 'localStorage', {
    value: stub,
    configurable: true,
  })
  if ((globalThis as unknown) !== (window as unknown)) {
    Object.defineProperty(globalThis, 'localStorage', {
      value: stub,
      configurable: true,
    })
  }
  return stub
}
