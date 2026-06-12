/**
 * Calculate age from a birth date string (ISO format YYYY-MM-DD)
 */
export const calculateAge = (birthDateString: string | null | undefined): number | null => {
  if (!birthDateString) return null;
  
  const birthDate = new Date(birthDateString);
  if (isNaN(birthDate.getTime())) return null;
  
  const today = new Date();
  let age = today.getFullYear() - birthDate.getFullYear();
  const m = today.getMonth() - birthDate.getMonth();
  
  if (m < 0 || (m === 0 && today.getDate() < birthDate.getDate())) {
    age--;
  }
  
  return age;
};

/**
 * Format age as a string with "years" or "months" if under 2 years old
 */
export const formatAge = (birthDateString: string | null | undefined): string => {
  const age = calculateAge(birthDateString);
  if (age === null) return '—';
  
  if (age < 2) {
    const birthDate = new Date(birthDateString!);
    const today = new Date();
    const months = (today.getFullYear() - birthDate.getFullYear()) * 12 + (today.getMonth() - birthDate.getMonth());
    return months === 1 ? '1 month' : `${months} months`;
  }
  
  return `${age} years`;
};
