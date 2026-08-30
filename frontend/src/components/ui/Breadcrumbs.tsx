import { Link } from 'react-router-dom';
import {
  Breadcrumbs as LibraryBreadcrumbs,
  type BreadcrumbLinkProps,
  type BreadcrumbsProps,
} from '@neuronection/assistant-ui';

/** App-side wrapper: routes library breadcrumb links through react-router. */
function RouterLink({ href, className, title, children }: BreadcrumbLinkProps) {
  return (
    <Link to={href} className={className} title={title}>
      {children}
    </Link>
  );
}

export function Breadcrumbs(props: Omit<BreadcrumbsProps, 'linkComponent'>) {
  return <LibraryBreadcrumbs {...props} linkComponent={RouterLink} />;
}

export default Breadcrumbs;
