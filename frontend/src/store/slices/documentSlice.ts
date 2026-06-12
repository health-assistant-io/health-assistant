import { create } from 'zustand';

interface Document {
  id: string;
  filename: string;
  file_path: string;
  status: string;
  created_at: string;
  patient_id?: string;
  progress?: number;
}

interface DocumentState {
  documents: Document[];
  currentDocument: Document | null;
  addDocument: (document: Document) => void;
  setDocuments: (documents: Document[]) => void;
  setCurrentDocument: (document: Document | null) => void;
  updateDocumentStatus: (documentId: string, status: string, progress?: number) => void;
  loadDocuments: () => void;
}

export const useDocumentStore = create<DocumentState>((set) => {
  // Load from localStorage on init
  const storedDocs = typeof window !== 'undefined' 
    ? localStorage.getItem('documents') 
    : null;
  const initialDocs: Document[] = storedDocs ? JSON.parse(storedDocs) : [];

  return {
    documents: initialDocs,
    currentDocument: null,
    
    addDocument: (document: Document) => {
      set((state) => {
        const newDocs = [...state.documents, document];
        localStorage.setItem('documents', JSON.stringify(newDocs));
        return { documents: newDocs };
      });
    },
    
    setDocuments: (documents: Document[]) => {
      set({ documents });
      if (typeof window !== 'undefined') {
        localStorage.setItem('documents', JSON.stringify(documents));
      }
    },
    
    setCurrentDocument: (document: Document | null) => set({ currentDocument: document }),
    
    updateDocumentStatus: (documentId: string, status: string, progress?: number) => {
      set((state) => {
        const newDocs = state.documents.map(doc =>
          doc.id === documentId ? { ...doc, status, progress: progress ?? doc.progress } : doc
        );
        localStorage.setItem('documents', JSON.stringify(newDocs));
        return { documents: newDocs };
      });
    },
    
    loadDocuments: () => {
      const storedDocs = typeof window !== 'undefined' 
        ? localStorage.getItem('documents') 
        : null;
      if (storedDocs) {
        set({ documents: JSON.parse(storedDocs) });
      }
    }
  };
});