import { useCallback, useEffect, useState } from 'react';
import {
  createExaminationCategory,
  getExaminationCategories,
} from '../services/examinationService';
import { createDoctor, listDoctors, type Doctor } from '../services/doctorService';
import {
  createOrganization,
  listOrganizations,
  type Organization,
} from '../services/organizationService';

export interface ExaminationReferences {
  categories: any[];
  doctors: Doctor[];
  organizations: Organization[];
  createCategory: (name: string) => Promise<any>;
  createDoctor: (name: string) => Promise<Doctor>;
  createOrganization: (name: string) => Promise<Organization>;
}

/**
 * Reference data for an examination form — categories, doctors and
 * organizations — with create-inline helpers that append to the local lists.
 * Selection stays with the caller.
 */
export function useExaminationReferences(): ExaminationReferences {
  const [categories, setCategories] = useState<any[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getExaminationCategories(), listDoctors(), listOrganizations()])
      .then(([cats, docs, orgs]) => {
        if (cancelled) return;
        setCategories(Array.isArray(cats) ? cats : []);
        setDoctors(Array.isArray(docs) ? docs : []);
        setOrganizations(Array.isArray(orgs) ? orgs : []);
      })
      .catch((err) => {
        console.error('Failed to load examination references', err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const createCategory = useCallback(async (name: string) => {
    const created = await createExaminationCategory({
      name,
      slug: name.toLowerCase().replace(/\s+/g, '-'),
    });
    setCategories((prev) => [...prev, created]);
    return created;
  }, []);

  const createDoctorRef = useCallback(async (name: string) => {
    const created = await createDoctor({ name });
    setDoctors((prev) => [...prev, created]);
    return created;
  }, []);

  const createOrganizationRef = useCallback(async (name: string) => {
    const created = await createOrganization({ name, active: true });
    setOrganizations((prev) => [...prev, created]);
    return created;
  }, []);

  return {
    categories,
    doctors,
    organizations,
    createCategory,
    createDoctor: createDoctorRef,
    createOrganization: createOrganizationRef,
  };
}

export default useExaminationReferences;
