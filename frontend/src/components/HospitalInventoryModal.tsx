import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import Modal, { ghostButtonClass, primaryButtonClass } from "./Modal";
import { toApiError } from "../lib/api";
import {
  BLOOD_LABEL,
  getInventory,
  putInventory,
  type BloodType,
} from "../lib/domain";

export default function HospitalInventoryModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["hospital", "inventory"], queryFn: getInventory, enabled: open });
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    if (q.data) setDraft(Object.fromEntries(q.data.map((i) => [i.blood_type, i.units])));
  }, [q.data]);

  const save = useMutation({
    mutationFn: (items: { blood_type: BloodType; units: number }[]) => putInventory(items),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hospital"] });
      onClose();
    },
    onError: (e) => setError(toApiError(e).detail),
  });

  return (
    <Modal open={open} onClose={onClose} title="Blood inventory" width="max-w-md">
      {q.isLoading ? (
        <div className="flex items-center gap-2 text-on-surface-variant">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : q.isError ? (
        // Phase 19: a failed fetch previously fell through to the "else"
        // branch below with `q.data` still undefined, which rendered an
        // empty inventory grid -- identical to "this hospital genuinely
        // has zero units of every type," not "the request failed." Real
        // finding from the Phase 19 hospital/offline E2E audit.
        <div className="text-center py-6">
          <p className="text-error text-body-sm mb-3">Couldn't load your inventory.</p>
          <button
            type="button"
            className="text-secondary text-sm font-label-md hover:underline"
            onClick={() => q.refetch()}
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            {(q.data ?? []).map((i) => (
              <label key={i.blood_type} className="flex items-center justify-between gap-2">
                <span className="font-bold text-primary w-10">{BLOOD_LABEL[i.blood_type]}</span>
                <input
                  type="number"
                  min={0}
                  className="w-24 px-2 py-1.5 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                  value={draft[i.blood_type] ?? 0}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      [i.blood_type]: Math.max(0, Number(e.target.value) || 0),
                    }))
                  }
                />
              </label>
            ))}
          </div>
          {error && <p className="text-error text-body-sm">{error}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button className={ghostButtonClass} onClick={onClose}>
              Cancel
            </button>
            <button
              className={primaryButtonClass}
              disabled={save.isPending}
              onClick={() =>
                save.mutate(
                  Object.entries(draft).map(([blood_type, units]) => ({
                    blood_type: blood_type as BloodType,
                    units,
                  })),
                )
              }
            >
              {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Save inventory
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
