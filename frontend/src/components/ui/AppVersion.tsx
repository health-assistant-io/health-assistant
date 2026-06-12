import React from 'react';
import { Link } from 'react-router-dom';
import packageJson from '../../../package.json';

interface AppVersionProps {
  collapsed?: boolean;
  className?: string;
}

export const AppVersion: React.FC<AppVersionProps> = ({ collapsed = false, className = '' }) => {
  const version = collapsed ? `v${packageJson.version.split('-')[0]}` : `v${packageJson.version}`;
  
  return (
    <div className={`text-center select-none ${className}`}>
      <Link 
        to="/about"
        className="text-[10px] font-semibold text-gray-400 dark:text-dark-muted tracking-wider hover:text-blue-500 transition-colors"
      >
        {version}
      </Link>
    </div>
  );
};

export default AppVersion;
