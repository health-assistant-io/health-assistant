import React, { useEffect, useId } from 'react';
import { useUIStore } from '../../store/slices/uiSlice';
import { StickyToolbar } from './StickyToolbar';

export interface BreadcrumbItem {
  label: string;
  path?: string;
  icon?: React.ReactNode;
}

interface PageHeaderProps {
  title: string;
  subtitle?: string | React.ReactNode;
  icon?: React.ReactNode;
  details?: React.ReactNode;
  actions?: React.ReactNode;
  center?: React.ReactNode;
  breadcrumbs?: BreadcrumbItem[];
  showBackButton?: boolean;
  sticky?: boolean;
  className?: string; 
}

export const PageHeader: React.FC<PageHeaderProps> = (props) => {
  const setPageHeaderConfig = useUIStore(state => state.setPageHeaderConfig);
  const instanceId = useId();

  useEffect(() => {
    // Sync metadata to the global header
    // We use an effect to avoid redundant updates during render
    
    // We get the current state without subscribing to avoid infinite loops
    // if the parent component re-renders when the UI store changes.
    const currentConfig = useUIStore.getState().pageHeaderConfig;
    
    // Check if update is actually needed to prevent infinite loops
    if (
      currentConfig?.instanceId === instanceId &&
      currentConfig?.title === props.title &&
      currentConfig?.subtitle === props.subtitle &&
      currentConfig?.icon === props.icon &&
      JSON.stringify(currentConfig?.breadcrumbs) === JSON.stringify(props.breadcrumbs) &&
      currentConfig?.showBackButton === props.showBackButton
    ) {
      return;
    }

    setPageHeaderConfig({
      instanceId,
      title: props.title,
      subtitle: props.subtitle,
      icon: props.icon,
      breadcrumbs: props.breadcrumbs,
      showBackButton: props.showBackButton,
    });
  }, [
    instanceId,
    props.title, 
    props.subtitle, 
    props.icon, 
    JSON.stringify(props.breadcrumbs),
    props.showBackButton,
    setPageHeaderConfig
  ]);

  // Clean up only on true unmount of the PageHeader component
  useEffect(() => {
    return () => {
      // Check if the global state still contains OUR instanceId before clearing
      // to avoid clearing the header of a newly mounted page (race condition fix)
      const current = useUIStore.getState().pageHeaderConfig;
      if (current?.instanceId === instanceId) {
        setPageHeaderConfig(null);
      }
    };
  }, [instanceId, setPageHeaderConfig]);

  // Render the toolbar locally if it has content
  if (props.actions || props.details || props.center) {
    return (
      <StickyToolbar 
        actions={props.actions}
        details={props.details}
        center={props.center}
        sticky={props.sticky ?? true}
        className={props.className}
      />
    );
  }

  return null;
};

export default PageHeader;