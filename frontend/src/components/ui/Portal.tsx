import React from 'react';
import { createPortal } from 'react-dom';

interface PortalProps {
  children: React.ReactNode;
}

/**
 * A simple Portal component that renders children at the end of document.body.
 * Useful for modals and overlays to escape parent stacking contexts.
 */
export const Portal: React.FC<PortalProps> = ({ children }) => {
  return createPortal(children, document.body);
};
