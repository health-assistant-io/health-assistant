export const getExamCategory = (exam: any) => {
  if (exam.category_concept) return exam.category_concept.name;
  if (exam.category) return exam.category;
  
  // Fallback for older exams without category
  const text = (exam.notes || '').toLowerCase();
  if (text.includes('x-ray') || text.includes('mri') || text.includes('ct scan') || text.includes('imaging')) return 'Imaging & Radiology';
  if (text.includes('blood') || text.includes('lab') || text.includes('test') || text.includes('culture')) return 'Laboratory Tests';
  if (text.includes('surgery') || text.includes('operation')) return 'Pathology';
  return 'Other';
};

export const getCategoryStyles = (exam: any) => {
  const categoryName = getExamCategory(exam);
  const details = exam.category_concept;

  if (details && details.color) {
    if (details.color.startsWith('#')) {
      return {
        style: { 
          backgroundColor: `${details.color}20`, 
          color: details.color,
          borderColor: details.color 
        },
        className: 'px-2 py-0.5 text-[10px] font-bold rounded uppercase border'
      };
    }
  }

  const classes = (() => {
    switch(categoryName) {
      case 'Imaging & Radiology': return 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 ring-purple-500';
      case 'Laboratory Tests': return 'bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 ring-orange-500';
      case 'Pathology': return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 ring-red-500';
      case 'Cardiology': return 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 ring-red-400';
      case 'Neurology': return 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 ring-indigo-500';
      case 'Other': return 'bg-gray-100 dark:bg-dark-bg text-gray-700 dark:text-dark-muted ring-gray-500';
      default: return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 ring-blue-500';
    }
  })();

  return { className: `px-2 py-0.5 text-[10px] font-bold rounded uppercase ${classes}` };
};

export const stripHtml = (html: string) => {
  if (!html) return '';
  const tmp = document.createElement("DIV");
  tmp.innerHTML = html;
  return tmp.textContent || tmp.innerText || '';
};
