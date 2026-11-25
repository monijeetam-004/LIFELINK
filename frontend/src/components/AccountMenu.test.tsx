/**
 * Phase J -- AccountMenu keyboard/disclosure behaviour.
 * Mocking boundary: only `../lib/auth`'s `useAuth` (the signed-in user).
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import AccountMenu from "./AccountMenu";
import { renderWithProviders } from "../test/test-utils";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: {
      id: "u1", email: "ada@example.com", full_name: "Ada Lovelace", role: "DONOR",
      is_email_verified: true,
    },
    logout: vi.fn(),
  }),
}));

describe("AccountMenu", () => {
  it("exposes expanded state, closes on Escape and returns focus to its toggle", () => {
    renderWithProviders(<AccountMenu />);
    const toggle = screen.getByRole("button", { name: /Ada/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Sign out/ })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("button", { name: /Sign out/ })).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });
});
