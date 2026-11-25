import { Link } from "react-router-dom";
import { ServerCrash } from "lucide-react";

/** 500 — a request reached the server but it failed. */
export default function ServerError() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-margin-mobile">
      <div className="text-center max-w-md">
        <ServerCrash className="h-12 w-12 text-error mx-auto mb-4" />
        <p className="font-display-lg text-display-lg text-error">500</p>
        <h1 className="font-headline-md text-headline-md text-on-surface mt-2">Something went wrong on our end</h1>
        <p className="text-on-surface-variant mt-2">
          This isn't something you did — our team has been notified. Please try again in a moment.
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-lg transition-all"
          >
            Try again
          </button>
          <Link
            to="/"
            className="border border-border-subtle text-on-surface font-label-md text-label-md px-6 py-3 rounded-lg hover:bg-surface-container-low transition-colors"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}
