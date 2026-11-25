import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, AlertTriangle, Info, XCircle, X } from "lucide-react";

export type ToastVariant = "success" | "error" | "warning" | "info";

export interface ToastInput {
  title: string;
  description?: string;
  variant?: ToastVariant;
  /** ms before auto-dismiss; 0 disables auto-dismiss. Default 5000. */
  duration?: number;
}

interface ToastItem extends Required<Pick<ToastInput, "title" | "variant" | "duration">> {
  id: string;
  description?: string;
}

interface ToastContextValue {
  toast: (input: ToastInput) => string;
  dismiss: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

const ICONS: Record<ToastVariant, typeof CheckCircle2> = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
};

const VARIANT_CLASS: Record<ToastVariant, string> = {
  success: "border-success/30 bg-surface text-on-surface",
  error: "border-error/30 bg-surface text-on-surface",
  warning: "border-warning/30 bg-surface text-on-surface",
  info: "border-secondary/30 bg-surface text-on-surface",
};

const ICON_CLASS: Record<ToastVariant, string> = {
  success: "text-success",
  error: "text-error",
  warning: "text-warning",
  info: "text-secondary",
};

/**
 * App-wide toast notification system. Mount <ToastProvider> once near the
 * root (see main.tsx) and call useToast() anywhere to surface a transient
 * message. Never used to fake success for an action that didn't actually
 * happen server-side — callers fire a toast only from a real mutation
 * result (onSuccess/onError), same as the rest of LifeLink's honesty rule.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const toast = useCallback(
    (input: ToastInput) => {
      const id = crypto.randomUUID();
      const duration = input.duration ?? 5000;
      const item: ToastItem = {
        id,
        title: input.title,
        description: input.description,
        variant: input.variant ?? "info",
        duration,
      };
      setItems((prev) => [...prev, item]);
      if (duration > 0) {
        const timer = setTimeout(() => dismiss(id), duration);
        timers.current.set(id, timer);
      }
      return id;
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast, dismiss }), [toast, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 w-[calc(100vw-2rem)] max-w-sm print:hidden"
        role="region"
        aria-label="Notifications"
      >
        <AnimatePresence>
          {items.map((item) => {
            const Icon = ICONS[item.variant];
            return (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, y: 12, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, x: 40 }}
                transition={{ duration: 0.2 }}
                role={item.variant === "error" ? "alert" : "status"}
                className={`rounded-xl border shadow-level-2 px-4 py-3 flex items-start gap-3 ${VARIANT_CLASS[item.variant]}`}
              >
                <Icon className={`h-5 w-5 mt-0.5 flex-shrink-0 ${ICON_CLASS[item.variant]}`} />
                <div className="min-w-0 flex-1">
                  <p className="font-label-md text-label-md text-on-surface">{item.title}</p>
                  {item.description && (
                    <p className="text-body-sm text-on-surface-variant mt-0.5">{item.description}</p>
                  )}
                </div>
                <button
                  type="button"
                  aria-label="Dismiss"
                  onClick={() => dismiss(item.id)}
                  className="p-1 rounded-full text-on-surface-variant hover:bg-surface-container-low flex-shrink-0"
                >
                  <X className="h-4 w-4" />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}
