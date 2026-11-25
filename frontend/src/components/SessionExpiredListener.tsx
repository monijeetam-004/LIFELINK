import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Bridges the window-level `lifelink:session-expired` event (dispatched by
 * lib/api.ts's response interceptor on an unexpected 401) to an actual
 * navigation, since the interceptor itself has no router context. Mounted
 * once near the top of <App/>.
 */
export default function SessionExpiredListener() {
  const navigate = useNavigate();
  // Avoid double-navigating if several requests 401 in quick succession.
  const handledRef = useRef(false);

  useEffect(() => {
    function onExpired(e: Event) {
      if (handledRef.current) return;
      handledRef.current = true;
      const from = (e as CustomEvent<{ from?: string }>).detail?.from;
      navigate("/session-expired", { replace: true, state: { from } });
      setTimeout(() => {
        handledRef.current = false;
      }, 2000);
    }
    window.addEventListener("lifelink:session-expired", onExpired);
    return () => window.removeEventListener("lifelink:session-expired", onExpired);
  }, [navigate]);

  return null;
}
