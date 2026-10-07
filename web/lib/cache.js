// Tiny in-process LRU for snapshot reads. Keys include the run id *and* its finished_at,
// so re-running a week (same run id, new snapshot) never serves stale data.
const MAX = 200;
const store = new Map();

export async function memo(key, fn) {
  // In development the code behind a key changes on every edit, so don't memoise.
  if (process.env.NODE_ENV !== "production") return fn();
  if (store.has(key)) {
    const v = store.get(key);
    store.delete(key);
    store.set(key, v);
    return v;
  }
  const value = await fn();
  store.set(key, value);
  if (store.size > MAX) store.delete(store.keys().next().value);
  return value;
}

export function runKey(name, run, ...parts) {
  return [name, run.id, run.finishedAt ?? "", ...parts].join(":");
}
