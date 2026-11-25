import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import L from "leaflet";
import { Circle, MapContainer, Marker, Popup, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";

import { BLOOD_LABEL, getMapData, type BloodType } from "../lib/domain";
import { useLiveFallbackInterval } from "../lib/realtime";

const BT_COLOR: Record<BloodType, string> = {
  O_NEG: "#b7131a", O_POS: "#db322f",
  A_NEG: "#005db7", A_POS: "#3b82f6",
  B_NEG: "#7c3aed", B_POS: "#a855f7",
  AB_NEG: "#0f766e", AB_POS: "#14b8a6",
};

const DEFAULT_CENTER: [number, number] = [40.73, -73.99];

function dot(color: string): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<span style="display:block;width:14px;height:14px;border-radius:9999px;background:${color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)"></span>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

/**
 * Live donor map. Replaces the static Stitch map image. Shows the hospital's own
 * location plus anonymised available-donor pins (coarse ~1km coordinates, blood
 * type only — no identity) served by `/map/data`.
 */
export default function DonorMap({ bloodType }: { bloodType?: BloodType }) {
  const q = useQuery({
    queryKey: ["map", "data", bloodType ?? "all"],
    queryFn: () => getMapData(bloodType),
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(60_000),
  });

  const center: [number, number] = q.data?.center
    ? [q.data.center.lat, q.data.center.lng]
    : DEFAULT_CENTER;

  // Gap elimination #25: when map tiles cannot load (offline, blocked by a
  // firewall or a CSP), the map is NOT left as a silent grey box: a banner
  // says so, and the pins/legend below still render on the plain canvas.
  const [tileErrors, setTileErrors] = useState(0);
  const [tilesLoaded, setTilesLoaded] = useState(0);
  const tilesFailed = tileErrors > 0 && tilesLoaded === 0;
  const tileUrl = import.meta.env.VITE_MAP_TILE_URL ?? "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

  const legend = useMemo(() => {
    const set = new Set((q.data?.donors ?? []).map((d) => d.blood_type));
    return Array.from(set);
  }, [q.data]);

  return (
    <div className="relative w-full h-full">
      <MapContainer
        key={center.join(",")}
        center={center}
        zoom={12}
        scrollWheelZoom={false}
        style={{ width: "100%", height: "100%" }}
      >
        <TileLayer
          attribution='&copy; OpenStreetMap contributors'
          url={tileUrl}
          eventHandlers={{
            tileerror: () => setTileErrors((n) => n + 1),
            tileload: () => setTilesLoaded((n) => n + 1),
          }}
        />
        {q.data?.center && (
          <>
            <Circle
              center={center}
              radius={2500}
              pathOptions={{ color: "#b7131a", fillColor: "#b7131a", fillOpacity: 0.06 }}
            />
            <Marker position={center} icon={dot("#111827")} title="Your hospital">
              <Popup>Your hospital</Popup>
            </Marker>
          </>
        )}
        {(q.data?.donors ?? []).map((d, i) => (
          <Marker
            key={`${d.latitude},${d.longitude},${i}`}
            position={[d.latitude, d.longitude]}
            icon={dot(BT_COLOR[d.blood_type])}
            // Phase 27: Leaflet's own `_initIcon` (leaflet-src.js) sets
            // `icon.title = options.title` on the marker's real DOM node --
            // including for a custom `L.divIcon` like ours, not just its
            // default `<img>` icon -- and a `title` attribute is a valid
            // accessible-name source for an element with `role="button"`
            // (which Leaflet also adds here since `keyboard: true` is its
            // default). Phase 26 disclosed this whole marker layer as
            // unfixable third-party DOM (axe's `aria-command-name`,
            // 1,133 nodes) because the rendered `.leaflet-marker-icon`
            // elements are indeed Leaflet-generated, not app markup -- but
            // re-reading Leaflet's real source this phase found the app
            // already controls exactly the option (`title`) needed to name
            // them, via a first-class, documented Marker prop, with no
            // patch to the library itself. Confirmed by re-running axe
            // after this change: 0 aria-command-name violations remain on
            // this route.
            title={`Available donor · ${BLOOD_LABEL[d.blood_type]} (approx. location)`}
          >
            <Popup>Available donor · {BLOOD_LABEL[d.blood_type]} (approx. location)</Popup>
          </Marker>
        ))}
      </MapContainer>

      {tilesFailed && (
        <div
          role="status"
          className="absolute top-2 left-2 right-2 z-[600] rounded bg-amber-50 border border-amber-300 text-amber-900 text-xs px-3 py-2"
        >
          Map background tiles could not be loaded (offline or blocked). Donor positions are still
          shown below on a blank canvas; distances elsewhere are unaffected.
        </div>
      )}

      {q.isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-surface/60 text-sm text-on-surface-variant">
          Loading map…
        </div>
      )}

      <div className="absolute bottom-4 left-4 z-[500] bg-surface p-2 rounded shadow-lg border border-border-subtle">
        <p className="text-[10px] font-bold text-on-surface-variant uppercase mb-1">
          Available donors ({q.data?.donors.length ?? 0})
        </p>
        {legend.length === 0 && (
          <p className="text-[10px] text-on-surface-variant">No donors sharing location yet</p>
        )}
        {legend.map((bt) => (
          <div key={bt} className="flex items-center gap-2">
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: BT_COLOR[bt] }}
            />
            <span className="text-[10px]">{BLOOD_LABEL[bt]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
