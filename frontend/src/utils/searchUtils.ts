/**
 * Centralized utility for searching biomarkers based on name, slug, and aliases.
 */

interface SearchableBiomarker {
  name?: string;
  displayName?: string;
  slug?: string | null;
  aliases?: string[];
}

/**
 * Checks if a biomarker matches a search term.
 * Searches across name, slug, and all aliases.
 */
export const matchBiomarker = <T extends SearchableBiomarker>(
  biomarker: T,
  searchTerm: string
): boolean => {
  if (!searchTerm) return true;
  
  const term = searchTerm.toLowerCase().trim();
  const name = (biomarker.name || biomarker.displayName || '').toLowerCase();
  const slug = (biomarker.slug || '').toLowerCase();
  const aliases = (biomarker.aliases || []).map(a => a.toLowerCase());
  
  return (
    name.includes(term) ||
    slug.includes(term) ||
    aliases.some(alias => alias.includes(term))
  );
};

/**
 * Filters a list of biomarkers based on a search term.
 */
export const filterBiomarkers = <T extends SearchableBiomarker>(
  biomarkers: T[],
  searchTerm: string
): T[] => {
  if (!searchTerm) return biomarkers;
  return biomarkers.filter(b => matchBiomarker(b, searchTerm));
};
