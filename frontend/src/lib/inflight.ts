import { useCallback, useState } from "react";

/**
 * Phase G -- per-item in-flight tracking for list actions (e.g. "mark this
 * notification read"). A single `mutation.isPending` flag can't lock just
 * the clicked row, so rows had no guard at all and a double-click sent two
 * requests. Wire `start` into the mutation's `onMutate` and `done` into
 * `onSettled`, then disable the row while `has(id)`.
 */
export function useInFlightIds() {
  const [ids, setIds] = useState<ReadonlySet<string>>(() => new Set());
  const start = useCallback((id: string) => setIds((s) => new Set(s).add(id)), []);
  const done = useCallback(
    (id: string) =>
      setIds((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      }),
    [],
  );
  const has = useCallback((id: string) => ids.has(id), [ids]);
  return { has, start, done };
}
