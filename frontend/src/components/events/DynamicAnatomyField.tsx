import React, { useState, useEffect } from 'react';
import { Network } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { AnatomySearchPopup } from '../anatomy/AnatomySearchPopup';
import { OrganPreview } from '../anatomy/OrganPreview';
import { AnatomyGraphModal } from '../anatomy/AnatomyGraphModal';
import { anatomyService } from '../../services/anatomyService';
import { markerForStructure, useAnatomyAtlas } from '../anatomy/atlas';
import type { AnatomyStructure } from '../../types/anatomy';

interface Props {
  value: string | undefined;
  onChange: (value: string) => void;
  placeholder?: string;
}

export const DynamicAnatomyField: React.FC<Props> = ({ value, onChange, placeholder }) => {
  const { t } = useTranslation();
  const [structure, setStructure] = useState<AnatomyStructure | null>(null);
  const [isGraphModalOpen, setIsGraphModalOpen] = useState(false);
  const figureOrder = useAnatomyAtlas((s) => s.figureOrder);
  const ensureLoaded = useAnatomyAtlas((s) => s.ensureLoaded);

  useEffect(() => {
    ensureLoaded();
  }, [ensureLoaded]);

  useEffect(() => {
    if (!value) {
      setStructure(null);
      return;
    }
    // Fetch the structure if we have a value but no structure (e.g., on initial load)
    if (!structure || structure.id !== value) {
      let isMounted = true;
      anatomyService.get(value).then((s) => {
        if (isMounted) setStructure(s);
      }).catch(() => {
        if (isMounted) setStructure(null);
      });
      return () => { isMounted = false; };
    }
  }, [value]);

  return (
    <div className="flex flex-col sm:flex-row gap-4 items-start w-full">
      <div className="flex-1 w-full relative">
        <AnatomySearchPopup
          selectedId={value}
          onSelect={(s) => {
            setStructure(s);
            onChange(s.id);
          }}
          placeholder={placeholder}
        />
      </div>
      
      {structure && (() => {
        const { figureSlug, marker } = markerForStructure(structure, figureOrder);
        return (
        <div className="w-full sm:w-28 flex-shrink-0 flex flex-col gap-2">
          <div className="bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl p-2 flex justify-center w-full">
            <OrganPreview figureSlug={figureSlug} marker={marker} label={structure.name} />
          </div>
          <button
            type="button"
            onClick={() => setIsGraphModalOpen(true)}
            className="flex items-center justify-center gap-1 w-full bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 py-1.5 px-2 rounded-xl text-[9px] font-black uppercase tracking-widest hover:bg-blue-100 transition-colors"
          >
            <Network className="w-3 h-3" />
            <span>{t('anatomy.view_graph')}</span>
          </button>

          <AnatomyGraphModal 
            isOpen={isGraphModalOpen} 
            onClose={() => setIsGraphModalOpen(false)} 
            initialStructure={structure} 
          />
        </div>
        );
      })()}
    </div>
  );
};
