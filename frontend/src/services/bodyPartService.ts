import { anatomyService } from './anatomyService';
import type { AnatomyStructure, AnatomyCategory } from '../types/anatomy';

/**
 * Backwards-compatible adapter over anatomyService.
 *
 * Historically body parts were a flat list; they now live in the anatomy graph
 * ontology. These helpers preserve the simplified {@link BodyPart} view used by
 * the clinical-event UI while delegating all HTTP traffic to anatomyService so
 * there is a single source of truth for the /anatomy endpoints.
 */
export interface BodyPart {
  id: string;
  name: string;
  slug: string;
  snomed_code?: string;
  description?: string;
  is_custom: boolean;
  tenant_id?: string;
}

export interface BodyPartCreate {
  name: string;
  snomed_code?: string;
  description?: string;
}

const toBodyPart = (s: AnatomyStructure): BodyPart => ({
  id: s.id,
  name: s.name,
  slug: s.slug,
  snomed_code: s.standard_system === 'snomed' ? s.standard_code ?? undefined : undefined,
  description: s.description ?? undefined,
  is_custom: s.is_custom,
  tenant_id: s.tenant_id ?? undefined,
});

export const listBodyParts = async (): Promise<BodyPart[]> => {
  const data = await anatomyService.list({ limit: 1000 });
  return data.items.map(toBodyPart);
};

export const createBodyPart = async (data: BodyPartCreate): Promise<BodyPart> => {
  const slug = data.name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  const s = await anatomyService.create({
    name: data.name,
    slug,
    category: 'OTHER' as AnatomyCategory,
    standard_system: 'snomed',
    standard_code: data.snomed_code ?? null,
    description: data.description ?? null,
    is_custom: true,
  });
  return toBodyPart(s);
};

export const getBodyPart = async (id: string): Promise<BodyPart> => {
  const s = await anatomyService.get(id);
  return toBodyPart(s);
};
