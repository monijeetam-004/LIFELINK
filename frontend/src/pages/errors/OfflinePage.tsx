import { useEffect, useState } from "react";
import { WifiOff } from "lucide-react";

/** Shown when the browser reports no network connection. */
export default function OfflinePage() {
  const [online, setOnline] = useState(navigator.onLine);

  useEffect(() => {
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-margin-mobile">
      <div className="text-center max-w-md">
        <WifiOff className="h-12 w-12 text-on-surface-variant mx-auto mb-4" />
        <h1 className="font-headline-md text-headline-md text-on-surface">You're offline</h1>
        <p className="text-on-surface-variant mt-2">
          LifeLink needs a connection to load live data. We'll reconnect automatically once you're back online.
        </p>
        <button
          type="button"
          disabled={!online}
          onClick={() => window.location.reload()}
          className="mt-6 bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-lg transition-all disabled:opacity-50"
        >
          {online ? "Back online — reload" : "Waiting for connection…"}
        </button>
      </div>
    </div>
  );
}
