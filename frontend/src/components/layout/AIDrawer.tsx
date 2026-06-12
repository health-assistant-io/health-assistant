import React from 'react';
import { AIChatInterface } from './AIChatInterface';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export const AIDrawer: React.FC<Props> = ({ isOpen, onClose }) => {
  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/20 backdrop-blur-[2px] z-[550] animate-in fade-in duration-300" 
        onClick={onClose}
      />
      
      {/* Drawer */}
      <div className="fixed top-0 right-0 h-screen w-full max-w-md bg-white dark:bg-dark-bg z-[560] shadow-[-20px_0_50px_rgba(0,0,0,0.1)] border-l border-gray-100 dark:border-dark-border flex flex-col animate-in slide-in-from-right duration-300">
        <AIChatInterface isFullScreen={false} onClose={onClose} />
      </div>
    </>
  );
};
