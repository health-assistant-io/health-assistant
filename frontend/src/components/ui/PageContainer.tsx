import React from 'react';

interface Props {
  children: React.ReactNode;
  className?: string;
}

export const PageContainer: React.FC<Props> = ({ children, className = '' }) => {
  return (
    <div className={`max-w-7xl mx-auto flex-1 flex flex-col min-h-0 w-full space-y-6 ${className}`}>
      {children}
    </div>
  );
};
