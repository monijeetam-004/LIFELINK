import { useEffect, useState } from "react";
import { WifiOff } from "lucide-react";

/** Slim top banner shown whenever the browser reports no connection. */
export default function OfflineBanner() {
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

  if (online) return null;

  return (
    <div
      role="status"
      className="fixed top-0 inset-x-0 z-[90] bg-on-surface text-inverse-on-surface text-body-sm flex items-center justify-center gap-2 py-2 print:hidden"
    >
      <WifiOff className="h-4 w-4" />
      You're offline — some actions won't work until your connection is back.
    </div>
  );
}
