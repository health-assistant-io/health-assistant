import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { Modal } from '../Modal';

/**
 * Focus: the stack-safe Escape behavior. Modal is used nested (picker detail
 * cards render a Modal on top of a form Modal), so Escape must close only the
 * topmost overlay — never the form behind it. Single-modal behavior is the
 * unchanged baseline.
 */
describe('Modal', () => {
  it('closes a single open modal on Escape', () => {
    const onClose = vi.fn();
    render(
      <Modal isOpen onClose={onClose} title="Test">
        body
      </Modal>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('nested: Escape closes only the topmost modal, not the parent', () => {
    const parentClose = vi.fn();
    const childClose = vi.fn();

    function App({ childOpen }: { childOpen: boolean }) {
      return (
        <Modal isOpen onClose={parentClose} title="Parent">
          {childOpen && (
            <Modal isOpen onClose={childClose} title="Child">
              child body
            </Modal>
          )}
        </Modal>
      );
    }

    // Open the parent first (its panel is portaled to document.body), then open
    // the child on top — mirrors real usage (a user clicks a detail-overlay
    // button from inside an already-open form modal), so the child portal is
    // appended after the parent and sits above it visually.
    const { rerender } = render(<App childOpen={false} />);
    rerender(<App childOpen={true} />);

    // Before the stack-safe fix, this fired BOTH closers.
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(childClose).toHaveBeenCalledTimes(1);
    expect(parentClose).not.toHaveBeenCalled();
  });

  it('does not react to non-Escape keys', () => {
    const onClose = vi.fn();
    render(
      <Modal isOpen onClose={onClose} title="Test">
        body
      </Modal>,
    );
    fireEvent.keyDown(document, { key: 'Enter' });
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
