import React from 'react';

interface CardProps {
  title?: string;
  children: React.ReactNode;
  className?: string;
}

const Card: React.FC<CardProps> = ({ title, children, className = '' }) => {
  return (
    <div className={`bg-white dark:bg-dark-surface rounded-lg shadow p-6 ${className}`}>
      {title && <h2 className="text-xl font-semibold text-gray-900 dark:text-dark-text mb-4">{title}</h2>}
      {children}
    </div>
  );
};

export default Card;