import React, { useEffect } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';
import { LoadingState } from '../../components/ui/LoadingState';

const OAuthConnected: React.FC = () => {
  const { domain } = useParams<{ domain: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const status = searchParams.get('status');
  const integrationId = searchParams.get('integration_id');

  useEffect(() => {
    if (status === 'connected') {
      toast.success(`Connected to ${domain}. You can now sync data.`);
    } else {
      toast.error('Authorization failed or was denied. Please try again.');
    }
    const target = integrationId
      ? `/settings/integrations/${integrationId}`
      : '/settings/integrations';
    const t = setTimeout(() => navigate(target, { replace: true }), 800);
    return () => clearTimeout(t);
  }, [status, domain, integrationId, navigate]);

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <LoadingState
        variant="section"
        message={status === 'connected' ? 'Finishing connection…' : 'Handling authorization…'}
      />
    </div>
  );
};

export default OAuthConnected;
