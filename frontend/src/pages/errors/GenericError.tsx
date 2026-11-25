import { AlertOctagon } from "lucide-react";

/** Last-resort fallback for an uncaught render error (see ErrorBoundary.tsx). */
export default function GenericError({
  message,
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-margin-mobile">
      <div className="text-center max-w-md">
        <AlertOctagon className="h-12 w-12 text-error mx-auto mb-4" />
        <h1 className="font-headline-md text-headline-md text-on-surface">Something went wrong</h1>
        <p className="text-on-surface-variant mt-2">
          {message ?? "An unexpected error occurred. You can try reloading the page."}
        </p>
        <button
          type="button"
          onClick={onRetry ?? (() => window.location.reload())}
          className="mt-6 bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-lg transition-all"
        >
          Reload
        </button>
      </div>
    </div>
  );
}
