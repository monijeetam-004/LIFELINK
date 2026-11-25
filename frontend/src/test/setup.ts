/**
 * Phase 32 -- global Vitest setup for the component-test layer.
 *
 * `@testing-library/jest-dom/vitest` registers the jest-dom matchers
 * (toBeInTheDocument, toBeDisabled, toHaveAccessibleName, etc.) directly
 * against Vitest's `expect`, including the TypeScript module augmentation --
 * no separate `@types/testing-library__jest-dom` shim needed.
 *
 * `cleanup()` (React Testing Library) unmounts whatever the previous test
 * rendered so tests never leak DOM/state into one another; RTL calls this
 * automatically when a global afterEach is available, which Vitest provides,
 * but it's registered explicitly here so that behavior doesn't depend on
 * @testing-library/react's own auto-detection continuing to work across
 * versions.
 */
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
