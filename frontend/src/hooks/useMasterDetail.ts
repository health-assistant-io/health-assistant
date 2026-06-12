import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

interface MasterDetailOptions {
  detailPath?: (id: string) => string;
  onSelect?: (id: string) => void;
  breakpoint?: number;
}

export const useMasterDetail = (options: MasterDetailOptions = {}) => {
  const { 
    detailPath, 
    onSelect, 
    breakpoint = 1024 
  } = options;
  
  const navigate = useNavigate();
  const [isLargeScreen, setIsLargeScreen] = useState(window.innerWidth >= breakpoint);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleResize = () => {
      setIsLargeScreen(window.innerWidth >= breakpoint);
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [breakpoint]);

  const handleItemClick = (id: string, item: any) => {
    if (isLargeScreen) {
      if (onSelect) {
        onSelect(id);
      }
    } else {
      if (detailPath) {
        navigate(detailPath(id));
      }
    }
  };

  return {
    isLargeScreen,
    handleItemClick,
    containerRef
  };
};
