/**
 * BiomarkerDetail "Patient Snapshot" tab — mobile-only summary surface that
 * mirrors the right sidebar of the desktop layout. Bundles the migration
 * progress banner (so it stays reachable when the sidebar is hidden) with the
 * BiomarkerSnapshotCard body. Renders the card body unwrapped — the enclosing
 * tab section already provides the card chrome, so an extra card-in-card would
 * look like duplicate panelling.
 */
import React from 'react';
import { MigrationProgressIndicator } from '../MigrationProgressIndicator';
import { BiomarkerSnapshotCard } from '../BiomarkerSnapshotCard';
import { type BiomarkerPrecisionProfile } from '../../../utils/biomarkerUtils';
import type { Biomarker } from '../../../types/biomarker';

export interface BiomarkerSnapshotTabProps {
  biomarker: Biomarker;
  trends: any[];
  precisionProfile: BiomarkerPrecisionProfile;
  interpretation: string;
  migrationStatus?: 'in_progress' | 'completed' | 'failed';
  migrationProgress?: number;
  migrationError?: string;
  onRetryMigration?: () => void;
}

export const BiomarkerSnapshotTab: React.FC<BiomarkerSnapshotTabProps> = ({
  biomarker,
  trends,
  precisionProfile,
  interpretation,
  migrationStatus,
  migrationProgress,
  migrationError,
  onRetryMigration,
}) => {
  return (
    <div className="p-6 sm:p-8 animate-in fade-in duration-300">
      <MigrationProgressIndicator
        status={migrationStatus}
        progress={migrationProgress}
        errorMessage={migrationError}
        onRetry={onRetryMigration}
      />
      <BiomarkerSnapshotCard
        biomarker={biomarker}
        trends={trends}
        precisionProfile={precisionProfile}
        interpretation={interpretation}
      />
    </div>
  );
};

export default BiomarkerSnapshotTab;
