import { useMemo, useCallback } from 'react';
import { BiomarkerObservation } from '../types/biomarker';
import { CATEGORY_MAPPING } from '../constants/categories';
import { getFinalStatus, isAbnormal } from '../utils/biomarkerUtils';
import { matchBiomarker } from '../utils/searchUtils';

export type Perspective = 'clinical' | 'technical' | 'examination';

export const formatGroupName = (name: string) => {
  return CATEGORY_MAPPING[name] || name.split(/[_-]/).map((word: string) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()).join(' ');
};

interface UseBiomarkersProps {
  documents?: any[];
  trendsData?: Record<string, any[]>;
  observations?: any[];
}

const generateSafeSlug = (name: string) => {
  return name?.toLowerCase().trim().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '') || 'unknown';
};

export function useBiomarkers({ documents = [], trendsData, observations = [] }: UseBiomarkersProps) {
  
  // Robust helper to extract min/max from various possible data structures
  const extractRange = (data: any) => {
    if (!data) return { min: null, max: null };
    
    // 0. If it's a string, try to parse it (e.g. "4.5 - 6.0")
    if (typeof data === 'string') {
      const match = data.match(/([0-9.]+)\s*-\s*([0-9.]+)/);
      if (match) {
        return { min: parseFloat(match[1]), max: parseFloat(match[2]) };
      }
      const gtMatch = data.match(/>\s*([0-9.]+)/);
      if (gtMatch) return { min: parseFloat(gtMatch[1]), max: null };
      const ltMatch = data.match(/<\s*([0-9.]+)/);
      if (ltMatch) return { min: null, max: parseFloat(ltMatch[1]) };
      return { min: null, max: null };
    }

    // 1. Direct min/max (lab_reference_range style or flat)
    let min = data.min ?? data.low ?? data.minimum ?? data.reference_range_min ?? data.biomarker_reference_range_min;
    let max = data.max ?? data.high ?? data.maximum ?? data.reference_range_max ?? data.biomarker_reference_range_max;

    // 2. FHIR style (list of objects with low/high)
    if ((min === null || min === undefined) && Array.isArray(data) && data.length > 0) {
      const first = data[0];
      min = first.low?.value ?? first.min;
      max = first.high?.value ?? first.max;
      
      // If still null, check if the first element has a 'text' or 'displayText'
      if ((min === null || min === undefined) && first.text) {
         return extractRange(first.text);
      }
    }

    // 3. Nested value_quantity style (rare but possible in some FHIR variants)
    if (typeof min === 'object' && min !== null) min = min.value;
    if (typeof max === 'object' && max !== null) max = max.value;

    return { 
      min: min !== undefined ? (typeof min === 'string' ? parseFloat(min) : min) : null, 
      max: max !== undefined ? (typeof max === 'string' ? parseFloat(max) : max) : null 
    };
  };

  const getRangeText = (min: number | null, max: number | null) => {
    if (min !== null && max !== null && !isNaN(min) && !isNaN(max)) return `${min} - ${max}`;
    if (min !== null && !isNaN(min)) return `> ${min}`;
    if (max !== null && !isNaN(max)) return `< ${max}`;
    return '--';
  };

  const biomarkers = useMemo(() => {
    const extracted: BiomarkerObservation[] = [];

    // 1. Process from trendsData (Longitudinal view)
    if (trendsData) {
      Object.entries(trendsData).forEach(([key, points]) => {
        if (!points || points.length === 0) return;
        
        const current = points[points.length - 1];
        const rawRange = extractRange(current);
        const standardRange = {
           min: current.standard_range_min ?? current.reference_range_min ?? null,
           max: current.standard_range_max ?? current.reference_range_max ?? null,
        };

        const rawRef = { ...rawRange, displayText: getRangeText(rawRange.min, rawRange.max) };
        const stdRef = { ...standardRange, displayText: getRangeText(standardRange.min, standardRange.max) || rawRef.displayText };

        const observation: BiomarkerObservation = {
          id: `trend-${key}`,
          displayName: current.name || key,
          slug: key,
          method: current.method || null,
          value: {
            raw: current.raw_value || current.value,
            normalized: current.value
          },
          unit: {
            rawSymbol: current.raw_unit || current.unit || '',
            normalizedSymbol: current.unit || ''
          },
          referenceRange: {
            ...(current.standard_range_min !== undefined ? stdRef : rawRef),
            raw: rawRef,
            standard: stdRef
          },
          relativeScore: current.relative_score || null,
          interpretation: current.status || 'Normal',
          source: {
            documentId: '',
            filename: current.examination_name || 'General Record',
            date: current.date
          },
          definitionId: current.biomarker_id || null,
          info: current.info || null,
          aliases: current.aliases || [],
          isTelemetry: current.source_type === 'telemetry' || points.some((p: any) => p.source_type === 'telemetry'),
          _rawJson: { 
            history: points,
            techCategory: current.technical_category,
            clinicalGroups: current.clinical_groups,
            examName: current.examination_name
          }
        };

        observation.interpretation = getFinalStatus(observation);
        extracted.push(observation);
      });
      return extracted;
    }

    // 2. Process from explicit observations (New standard)
    if (observations && observations.length > 0) {
      observations.forEach((obs, index) => {
        const name = obs.code?.text || 'Unknown';
        const slug = obs.biomarker_slug || generateSafeSlug(name);
        
        // Use robust extractor for raw range
        const rawRange = extractRange(obs.lab_reference_range || obs.reference_range);
        const standardRange = extractRange({
          min: obs.biomarker_reference_range_min,
          max: obs.biomarker_reference_range_max
        });

        const rawRef = { ...rawRange, displayText: getRangeText(rawRange.min, rawRange.max) };
        const stdRef = { ...standardRange, displayText: getRangeText(standardRange.min, standardRange.max) };

        const observation: BiomarkerObservation = {
          id: obs.id || `obs-${index}`,
          displayName: name,
          slug: slug,
          method: obs.method || null,
          value: {
            raw: obs.raw_value ?? obs.value_quantity?.value ?? 0,
            normalized: obs.normalized_value ?? obs.value_quantity?.value ?? 0
          },
          unit: {
            rawSymbol: obs.value_quantity?.unit || '',
            normalizedSymbol: obs.normalized_unit || obs.value_quantity?.unit || ''
          },
          referenceRange: {
            ...(obs.normalized_value ? stdRef : rawRef),
            raw: rawRef,
            standard: stdRef
          },
          relativeScore: obs.relative_score || null,
          interpretation: obs.interpretation || 'Normal',
          source: {
            documentId: obs.document_id || '',
            filename: 'Laboratory Result',
            date: obs.effective_datetime
          },
          definitionId: obs.biomarker_id || null,
          info: obs.biomarker_info || null,
          aliases: obs.biomarker_aliases || [],
          _rawJson: obs
        };
        
        observation.interpretation = getFinalStatus(observation);
        extracted.push(observation);
      });
      return extracted;
    }

    // 3. Process from documents (Visit/Document view)
    documents.forEach(doc => {
      if (!doc.entities) return;

      const source = {
        documentId: doc.id,
        filename: doc.filename,
        examinationId: doc.examination_id,
        date: doc.created_at
      };

      if (doc.entities.known_biomarkers && Array.isArray(doc.entities.known_biomarkers)) {
        doc.entities.known_biomarkers.forEach((b: any, index: number) => {
          const generatedSlug = b.matched_slug && b.matched_slug !== 'unknown' 
            ? b.matched_slug 
            : generateSafeSlug(b.name);

          const rawRange = extractRange(b);
          const standardRange = extractRange({
            min: b.biomarker_reference_range_min,
            max: b.biomarker_reference_range_max
          });

          const rawRef = { ...rawRange, displayText: getRangeText(rawRange.min, rawRange.max) };
          const stdRef = { ...standardRange, displayText: getRangeText(standardRange.min, standardRange.max) };

          const observation: BiomarkerObservation = {
            id: `${doc.id}-known-${index}`,
            displayName: b.name,
            slug: generatedSlug,
            method: b.method || null,
            value: { 
              raw: b.value, 
              normalized: b.normalized_value || b.value 
            },
            unit: { 
              rawSymbol: b.unit_symbol || '',
              normalizedSymbol: b.normalized_unit || b.unit_symbol || ''
            },
            referenceRange: {
              ...(b.normalized_value ? stdRef : rawRef),
              raw: rawRef,
              standard: stdRef
            },
            relativeScore: null,
            interpretation: b.interpretation_flag || 'Normal',
            source,
            definitionId: b.biomarker_id || null,
            info: null,
            _rawJson: { 
              ...b, 
              document_category: doc.entities.document_category,
              techCategory: doc.entities.document_category
            }
          };

          observation.interpretation = getFinalStatus(observation);
          extracted.push(observation);
        });
      }

      if (doc.entities.unknown_biomarkers && Array.isArray(doc.entities.unknown_biomarkers)) {
        doc.entities.unknown_biomarkers.forEach((b: any, index: number) => {
          const generatedSlug = generateSafeSlug(b.raw_name);
          const rawRange = extractRange(b);
          const rawRef = { ...rawRange, displayText: getRangeText(rawRange.min, rawRange.max) };

          const observation: BiomarkerObservation = {
            id: `${doc.id}-unknown-${index}`,
            displayName: b.raw_name,
            slug: generatedSlug,
            method: b.method || null,
            value: { raw: b.value, normalized: b.value },
            unit: { rawSymbol: b.unit_symbol || '' },
            referenceRange: {
              ...rawRef,
              raw: rawRef,
              standard: rawRef // For unknown biomarkers, raw is our only reference
            },
            relativeScore: null,
            interpretation: b.interpretation_flag || 'Normal',
            source,
            definitionId: b.biomarker_id || null,
            info: null,
            _rawJson: { 
              ...b, 
              document_category: doc.entities.document_category,
              techCategory: doc.entities.document_category
            }
          };

          observation.interpretation = getFinalStatus(observation);
          extracted.push(observation);
        });
      }

      // Legacy fallback
      if (doc.entities.biomarkers && Array.isArray(doc.entities.biomarkers) && !doc.entities.known_biomarkers) {
        doc.entities.biomarkers.forEach((b: any, index: number) => {
          const generatedSlug = generateSafeSlug(b.name);
          const observation: BiomarkerObservation = {
            id: `${doc.id}-legacy-${index}`,
            displayName: b.name,
            slug: generatedSlug,
            method: null,
            value: { raw: parseFloat(b.value) || 0, normalized: null },
            unit: { rawSymbol: b.unit || '' },
            referenceRange: { min: null, max: null, displayText: b.reference_range || '--' },
            relativeScore: null,
            interpretation: 'Normal',
            source,
            definitionId: b.biomarker_id || null,
            info: null,
            aliases: b.aliases || [],
            _rawJson: { 
              ...b, 
              document_category: doc.entities.document_category,
              techCategory: doc.entities.document_category
            }
          };

          observation.interpretation = getFinalStatus(observation);
          extracted.push(observation);
        });
      }
    });

    // Deduplicate exact same measurements from the same examination context
    const uniqueMap = new Map<string, BiomarkerObservation>();
    extracted.forEach(b => {
      const uniqueKey = `${b.slug}-${b.value.raw}-${b.source.documentId}`;
      if (!uniqueMap.has(uniqueKey)) {
        uniqueMap.set(uniqueKey, b);
      }
    });

    return Array.from(uniqueMap.values());
  }, [documents, trendsData, observations]);

  const getGroupedData = useCallback((perspective: Perspective, activeTab: string = 'All', searchTerm: string = '', showAlertsOnly: boolean = false) => {
    const filtered = biomarkers.filter(b => {
      const matchesSearch = matchBiomarker(b, searchTerm);
      const matchesAlert = !showAlertsOnly || isAbnormal(b.interpretation);
      return matchesSearch && matchesAlert;
    });

    const groups: Record<string, BiomarkerObservation[]> = {};

    filtered.forEach(b => {
      let groupNames: string[] = [];

      if (perspective === 'technical') {
        const techCat = b._rawJson?.techCategory || b._rawJson?.document_category || 'other';
        groupNames = [formatGroupName(techCat)];
      } else if (perspective === 'examination') {
        groupNames = [b.source.filename || 'General Record'];
      } else {
        const clinicalGroups = b._rawJson?.clinicalGroups;
        if (clinicalGroups && Array.isArray(clinicalGroups) && clinicalGroups.length > 0) {
          groupNames = clinicalGroups.map(g => formatGroupName(g));
        } else {
          const fallback = b._rawJson?.techCategory || b._rawJson?.document_category || 'other';
          groupNames = [formatGroupName(fallback)];
        }
      }

      if (groupNames.length === 0) {
        groupNames = [formatGroupName('other')];
      }

      groupNames.forEach(gName => {
        if (activeTab !== 'All' && gName !== activeTab) return;
        if (!groups[gName]) groups[gName] = [];
        groups[gName].push(b);
      });
    });

    return Object.entries(groups).sort((a, b) => a[0].localeCompare(b[0]));
  }, [biomarkers]);

  const getTabs = useCallback((perspective: Perspective) => {
    const categoriesInUse = new Set<string>();

    biomarkers.forEach(b => {
      if (perspective === 'technical') {
        categoriesInUse.add(formatGroupName(b._rawJson?.techCategory || b._rawJson?.document_category || 'other'));
      } else if (perspective === 'examination') {
        categoriesInUse.add(b.source.filename || 'General Record');
      } else {
        const clinicalGroups = b._rawJson?.clinicalGroups;
        if (clinicalGroups && Array.isArray(clinicalGroups) && clinicalGroups.length > 0) {
          clinicalGroups.forEach(g => categoriesInUse.add(formatGroupName(g)));
        } else {
          categoriesInUse.add(formatGroupName(b._rawJson?.techCategory || b._rawJson?.document_category || 'other'));
        }
      }
    });

    return ['All', ...Array.from(categoriesInUse).sort()];
  }, [biomarkers]);

  const getAbnormal = useCallback(() => {
    return biomarkers.filter(b => isAbnormal(b.interpretation));
  }, [biomarkers]);

  const groupByCategory = useCallback(() => {
    const groups: Record<string, BiomarkerObservation[]> = {};
    biomarkers.forEach(b => {
      const key = b.slug || b.displayName.toLowerCase();
      if (!groups[key]) groups[key] = [];
      groups[key].push(b);
    });
    return groups;
  }, [biomarkers]);

  return {
    biomarkers,
    getAbnormal,
    getGroupedData,
    groupByCategory,
    getTabs,
    totalCount: biomarkers.length
  };
}
