/**
 * Phase 32 -- component tests for the shared Modal primitive (used by every
 * real dialog in the app, including the certificate-revoke flow tested in
 * AdminAnalytics.test.tsx). No network calls exist in this component, so
 * there is nothing to mock here -- these are pure behavior/accessibility
 * tests, not a duplicate of Playwright's real-browser focus-trap coverage.
 */
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import Modal from "./Modal";

function renderOpenModal(onClose = vi.fn()) {
  return render(
    <Modal open onClose={onClose} title="Test dialog">
      <button>Inside action</button>
    </Modal>,
  );
}

describe("Modal", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <Modal open={false} onClose={vi.fn()} title="Test dialog">
        <p>content</p>
      </Modal>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders real dialog semantics when open", () => {
    renderOpenModal();
    const dialog = screen.getByRole("dialog", { name: "Test dialog" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("calls onClose when the close button is activated", () => {
    const onClose = vi.fn();
    renderOpenModal(onClose);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose on Escape", () => {
    const onClose = vi.fn();
    renderOpenModal(onClose);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("returns focus to the previously-focused element on close", () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button onClick={() => setOpen(true)}>Open</button>
          <Modal open={open} onClose={() => setOpen(false)} title="Test dialog">
            <button>Inside action</button>
          </Modal>
        </div>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(opener).toHaveFocus();
  });
});
