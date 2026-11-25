/**
 * Phase 32 -- component tests for the shared ConfirmationDialog wrapper.
 * No network calls in this component either -- pure prop-driven behavior.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import ConfirmationDialog from "./ConfirmationDialog";

describe("ConfirmationDialog", () => {
  it("calls onConfirm when the confirm button is activated", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmationDialog open onClose={vi.fn()} onConfirm={onConfirm} title="Suspend donor" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("disables both actions while busy, so a pending mutation can't be double-submitted", () => {
    render(
      <ConfirmationDialog open onClose={vi.fn()} onConfirm={vi.fn()} title="Suspend donor" busy />,
    );
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("surfaces a real errorMessage through alert semantics", () => {
    render(
      <ConfirmationDialog
        open
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        title="Suspend donor"
        errorMessage="You don't have permission to do that."
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("You don't have permission to do that.");
  });

  it("uses custom confirm/cancel labels when provided", () => {
    render(
      <ConfirmationDialog
        open
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        title="Revoke access"
        confirmLabel="Revoke"
        cancelLabel="Keep access"
      />,
    );
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep access" })).toBeInTheDocument();
  });
});
