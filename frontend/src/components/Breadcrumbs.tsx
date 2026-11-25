import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

export interface Crumb {
  label: string;
  to?: string;
}

/** Simple breadcrumb trail. Last item renders as plain text (current page). */
export default function Breadcrumbs({ items }: { items: Crumb[] }) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="Breadcrumb" className="flex items-center flex-wrap gap-1 text-body-sm">
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        return (
          <span key={`${item.label}-${i}`} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-on-surface-variant" />}
            {item.to && !isLast ? (
              <Link to={item.to} className="text-on-surface-variant hover:text-primary hover:underline">
                {item.label}
              </Link>
            ) : (
              <span className={isLast ? "text-on-surface font-label-md" : "text-on-surface-variant"}>
                {item.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
