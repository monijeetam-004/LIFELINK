/**
 * Phase 19: maps a notification's `type` to the page a viewer would
 * actually want to land on, when one exists as a real routed page (not a
 * drawer opened from in-page dashboard state, which a notification click
 * can't reconstruct). Deliberately small and conservative -- most
 * notification types (blood_request.*, emergency.*, family.*) are shown
 * via drawers/inline panels on each role's dashboard rather than a
 * dedicated URL, so there is no honest destination to link to for those
 * yet; mapping only what genuinely resolves to a real page avoids a
 * link that looks like it should work but silently does nothing.
 *
 * `verification.submitted` is the one Phase 19 explicitly needed a deep
 * link for (admin's shared notification surface, per
 * LIFELINK_PHASE_19_REPORT.md) and it has a real destination:
 * /admin/verifications. Deliberately NOT mapping `verification.approved`/
 * `verification.rejected` here even though they're the same "kind" of
 * event -- those two go to the *submitter* (a DONOR/HOSPITAL account),
 * who has no access to /admin/verifications at all; sending them there
 * would be a real authorization dead-end (a 403), not a convenience.
 */
export function resolveNotificationPath(type: string): string | null {
  switch (type) {
    case "verification.submitted":
      return "/admin/verifications";
    default:
      return null;
  }
}
