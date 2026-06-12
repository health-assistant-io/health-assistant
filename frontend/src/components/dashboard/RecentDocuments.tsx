interface RecentDocumentsProps {
  documents: Array<{
    id: string;
    filename: string;
    created_at: string;
  }>;
}

const RecentDocuments: React.FC<RecentDocumentsProps> = ({ documents }) => {
  if (documents.length === 0) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm p-6 border border-gray-100 dark:border-dark-border h-full flex flex-col">
        <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight mb-4">
          Recent Documents
        </h2>
        <div className="flex-1 flex flex-col items-center justify-center text-center py-10 opacity-40">
           <svg className="w-12 h-12 mb-2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
           <p className="text-sm font-medium">No recent documents available</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm p-6 border border-gray-100 dark:border-dark-border h-full flex flex-col">
      <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight mb-6">
        Recent Documents
      </h2>
      <div className="space-y-3 overflow-y-auto flex-1 custom-scrollbar">
        {documents.map((doc) => (
          <div
            key={doc.id}
            className="flex items-center justify-between p-4 border border-gray-50 dark:border-dark-border/50 bg-gray-50/30 dark:bg-dark-bg/30 rounded-xl hover:bg-white dark:hover:bg-dark-bg transition-all group"
          >
            <div className="overflow-hidden mr-4">
              <h3 className="font-bold text-sm text-gray-900 dark:text-dark-text truncate group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">{doc.filename}</h3>
              <p className="text-[10px] font-medium text-gray-400 dark:text-dark-muted mt-0.5">
                Uploaded {new Date(doc.created_at).toLocaleDateString()}
              </p>
            </div>
            <div className="flex items-center space-x-2 shrink-0">
              <span className="px-2 py-0.5 text-[9px] font-black uppercase tracking-tighter bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 rounded">
                Processed
              </span>
              <button 
                onClick={() => window.location.href = `/documents/${doc.id}`}
                className="p-2 text-gray-400 dark:text-dark-muted hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition-all"
                title="View Analysis"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default RecentDocuments;