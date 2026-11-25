import { type ReactNode } from "react";
import type { Crumb } from "./Breadcrumbs";
import Breadcrumbs from "./Breadcrumbs";

/**
 * Reusable page title block: optional breadcrumbs, title, description and a
 * right-aligned actions slot. Intended for new/updated pages that want a
 * consistent header without hand-rolling one — existing dashboard headers
 * are left as-is to avoid an unrelated visual refactor.
 */
export default function PageHeader({
  title,
  description,
  breadcrumbs,
  actions,
}: {
  title: string;
  description?: string;
  breadcrumbs?: Crumb[];
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 mb-6">
      {breadcrumbs && breadcrumbs.length > 0 && <Breadcrumbs items={breadcrumbs} />}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-headline-md text-headline-md text-on-surface">{title}</h1>
          {description && <p className="text-on-surface-variant text-body-sm mt-1">{description}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
      </div>
    </div>
  );
}
