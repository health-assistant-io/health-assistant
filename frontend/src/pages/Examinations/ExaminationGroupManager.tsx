import React, { useState, useEffect, useRef } from 'react';
import { 
  Plus, 
  Trash2, Calendar, Tag, 
  Check, Sparkles,
  ChevronRight, ChevronDown, Search, Camera
} from 'lucide-react';
import { getTempPreviewUrl } from '../../services/documentService';
import { FilePreviewManager } from '../../components/ui/FilePreviewManager';
import { FileCard } from '../../components/ui/FileCard';
import { Doctor } from '../../services/doctorService';
import { DoctorSelector } from '../../components/ui/DoctorSelector';
import { DynamicIcon } from '../../components/ui/DynamicIcon';
import { isMobileDevice } from '../../utils/deviceUtils';
import { DatePicker } from '../../components/ui/DatePicker';
import { AIBadge } from '../../components/ui/AIBadge';

export interface ExamGroup {
  id: string;
  name: string;
  date: string;
  category: string;
  doctorIds: string[];
  notes: string;
  patientNotes: string;
}

export interface FileWithGroup {
  id: string;
  file: File;
  groupId: string | null;
  includeInExtraction: boolean;
}

interface BulkUploadManagerProps {
  files: FileWithGroup[];
  setFiles: React.Dispatch<React.SetStateAction<FileWithGroup[]>>;
  groups: ExamGroup[];
  setGroups: React.Dispatch<React.SetStateAction<ExamGroup[]>>;
  availableDoctors: Doctor[];
  onAddDoctor: (name: string) => Promise<void>;
  isSmartMode?: boolean;
  isSingleMode?: boolean;
  categories?: any[];
}

export const ExaminationGroupManager: React.FC<BulkUploadManagerProps> = ({
  files,
  setFiles,
  groups,
  setGroups,
  availableDoctors,
  onAddDoctor,
  isSmartMode = false,
  isSingleMode = false,
  categories = []
}) => {
  const [activePreview, setActivePreview] = useState<{ url: string; name: string; type: string; isBackendProcessed?: boolean; localFile?: File } | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [draggedFileId, setDraggedFileId] = useState<string | null>(null);
  const [hoveredGroupId, setHoveredGroupId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

  const addGroup = () => {
    if (isSingleMode) return;
    const newGroup: ExamGroup = {
      id: Math.random().toString(36).substr(2, 9),
      name: `Examination ${groups.length + 1}`,
      date: new Date().toISOString().split('T')[0],
      category: 'Clinical',
      doctorIds: [],
      notes: '',
      patientNotes: ''
    };
    setGroups([...groups, newGroup]);
  };

  const removeGroup = (id: string) => {
    if (isSingleMode) return;
    setGroups(groups.filter(g => g.id !== id));
    setFiles(files.map(f => f.groupId === id ? { ...f, groupId: null } : f));
  };

  const updateGroup = (id: string, updates: Partial<ExamGroup>) => {
    setGroups(groups.map(g => g.id === id ? { ...g, ...updates } : g));
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const newFiles = Array.from(e.target.files).map(file => ({
        id: Math.random().toString(36).substr(2, 9),
        file,
        groupId: isSingleMode ? (groups[0]?.id || null) : null,
        includeInExtraction: true
      }));
      setFiles(prev => [...prev, ...newFiles]);
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleDragStart = (e: React.DragEvent, fileId: string) => {
    if (isSingleMode) return;
    setDraggedFileId(fileId);
    e.dataTransfer.setData('fileId', fileId);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDrop = (e: React.DragEvent, groupId: string | null) => {
    e.preventDefault();
    if (isSingleMode) return;
    const fileId = e.dataTransfer.getData('fileId') || draggedFileId;
    if (fileId) {
      setFiles(files.map(f => f.id === fileId ? { ...f, groupId } : f));
    }
    setDraggedFileId(null);
    setHoveredGroupId(null);
  };

  const removeFile = (id: string) => {
    setFiles(files.filter(f => f.id !== id));
  };

  const toggleFileInclusion = (id: string) => {
    setFiles(files.map(f => f.id === id ? { ...f, includeInExtraction: !f.includeInExtraction } : f));
  };

  const openPreview = async (file: File) => {
    const isDicom = file.name.toLowerCase().endsWith('.dcm');
    const isPdf = file.type === 'application/pdf';
    
    if (isDicom || isPdf) {
      setIsPreviewLoading(true);
      try {
        const result = await getTempPreviewUrl(file);
        setActivePreview({ 
          url: result.url, 
          name: file.name, 
          type: file.type,
          isBackendProcessed: true,
          localFile: file
        });
      } catch (err) {
        console.error("Failed to generate temp preview:", err);
        // Fallback to local URL if backend fails
        const url = URL.createObjectURL(file);
        setActivePreview({ url, name: file.name, type: file.type });
      } finally {
        setIsPreviewLoading(false);
      }
    } else {
      const url = URL.createObjectURL(file);
      setActivePreview({ url, name: file.name, type: file.type });
    }
  };

  const unassignedFiles = files.filter(f => f.groupId === null);

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Document Storage / Unassigned Area - Hidden in Single Mode */}
      {!isSingleMode && (
        <div 
          className={`relative p-6 bg-gray-50/50 dark:bg-dark-bg/30 border-2 border-dashed rounded-[2rem] transition-all cursor-pointer hover:bg-gray-100/50 dark:hover:bg-dark-bg/50 ${
            unassignedFiles.length === 0 ? 'border-gray-200 dark:border-dark-border' : 'border-blue-200 dark:border-blue-900/30 shadow-inner'
          }`}
          onDragOver={(e) => { e.preventDefault(); setHoveredGroupId('unassigned'); }}
          onDragLeave={() => setHoveredGroupId(null)}
          onDrop={(e) => handleDrop(e, null)}
          onClick={() => fileInputRef.current?.click()}
        >
          <div className="flex items-center justify-between mb-4 px-2" onClick={(e) => e.stopPropagation()}>
            <div className="flex flex-col">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-black uppercase tracking-widest text-gray-400">Unassigned Documents</h3>
                <AIBadge workflow="full_reconstruction" size="sm" showText={false} />
              </div>
              <p className="text-[10px] text-gray-500">Click anywhere or drag documents to add to staging area</p>
            </div>
            <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
              <button 
                type="button"
                onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}
                className="flex items-center gap-2 px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-xs font-bold text-gray-700 dark:text-dark-text hover:border-blue-500 transition-all shadow-sm"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add Files</span>
              </button>
              {isMobileDevice() && (
                <button 
                  type="button"
                  onClick={(e) => { e.stopPropagation(); cameraInputRef.current?.click(); }}
                  className="flex items-center gap-2 px-4 py-2 bg-white dark:bg-dark-surface border border-indigo-200 dark:border-indigo-900/30 rounded-xl text-xs font-bold text-indigo-600 dark:text-indigo-400 hover:border-indigo-500 transition-all shadow-sm"
                >
                  <Camera className="w-3.5 h-3.5" />
                  <span>Take Photo</span>
                </button>
              )}
            </div>
            <input 
              type="file" 
              ref={fileInputRef} 
              onChange={handleFileChange} 
              multiple 
              className="hidden" 
              accept=".pdf,.jpg,.jpeg,.png,.docx,.txt,.dcm"
            />
            <input 
              type="file" 
              ref={cameraInputRef} 
              onChange={handleFileChange} 
              accept="image/*"
              capture="environment"
              className="hidden" 
            />
          </div>

          {unassignedFiles.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 opacity-40">
              <div className="p-4 bg-gray-100 dark:bg-dark-surface rounded-full mb-3">
                <Plus className="w-8 h-8 text-gray-400" />
              </div>
              <span className="text-sm font-medium text-gray-500">Click or drag documents to begin</span>
            </div>
          ) : (
            <div className="flex flex-wrap gap-4">
              {unassignedFiles.map(f => (
                <FileCard 
                  key={f.id} 
                  file={f.file} 
                  onDragStart={(e) => handleDragStart(e, f.id)}
                  onRemove={() => removeFile(f.id)}
                  onPreview={() => openPreview(f.file)}
                  onToggleInclusion={() => toggleFileInclusion(f.id)}
                  includeInExtraction={f.includeInExtraction}
                  draggable={!isSingleMode}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Examination Bubbles Area */}
      <div className={`grid grid-cols-1 ${isSingleMode ? '' : 'lg:grid-cols-2'} gap-6`}>
        {groups.map((group, index) => (
          <ExaminationBubble 
            key={group.id}
            group={group}
            index={index}
            files={isSingleMode ? files : files.filter(f => f.groupId === group.id)}
            onDrop={(e) => handleDrop(e, group.id)}
            onDragStart={handleDragStart}
            onUpdate={(updates) => updateGroup(group.id, updates)}
            onRemove={() => removeGroup(group.id)}
            onPreview={openPreview}
            onRemoveFile={removeFile}
            onToggleFileInclusion={toggleFileInclusion}
            setFiles={setFiles}
            availableDoctors={availableDoctors}
            onAddDoctor={onAddDoctor}
            isHovered={hoveredGroupId === group.id}
            onHoverChange={(hovering) => setHoveredGroupId(hovering ? group.id : null)}
            isSmartMode={isSmartMode}
            isSingleMode={isSingleMode}
            categories={categories}
          />
        ))}

        {/* Add New Examination Button - Hidden in Single Mode */}
        {!isSingleMode && (
          <button
            type="button"
            onClick={addGroup}
            className="group relative flex flex-col items-center justify-center p-8 bg-white dark:bg-dark-surface border-2 border-dashed border-gray-200 dark:border-dark-border rounded-[2.5rem] hover:border-blue-400 dark:hover:border-blue-900 transition-all hover:shadow-xl hover:shadow-blue-500/5 active:scale-95 min-h-[300px]"
          >
            <div className="p-4 bg-blue-50 dark:bg-blue-900/20 rounded-full mb-4 group-hover:scale-110 transition-transform">
              <Plus className="w-8 h-8 text-blue-500" />
            </div>
            <span className="text-lg font-black text-gray-900 dark:text-dark-text tracking-tight">Add Examination Group</span>
            <p className="text-xs text-gray-400 mt-2 text-center max-w-[200px]">Create a new bubble to group related medical documents</p>
          </button>
        )}
      </div>

      {/* Previews */}
      {isPreviewLoading && (
        <div className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-300">
           <div className="flex flex-col items-center gap-6">
              <div className="animate-spin rounded-full h-16 w-16 border-t-2 border-b-2 border-indigo-500"></div>
              <p className="text-white font-black text-xs uppercase tracking-[0.3em] animate-pulse">Initializing Diagnostic Preview</p>
           </div>
        </div>
      )}

      {activePreview && (
        <FilePreviewManager 
          url={activePreview.url} 
          filename={activePreview.name} 
          type={activePreview.type} 
          isBackendProcessed={activePreview.isBackendProcessed}
          localFile={activePreview.localFile}
          onClose={() => {
            URL.revokeObjectURL(activePreview.url);
            setActivePreview(null);
          }} 
        />
      )}
    </div>
  );
};

const ExaminationBubble: React.FC<{
  group: ExamGroup;
  index: number;
  files: FileWithGroup[];
  onDrop: (e: React.DragEvent) => void;
  onDragStart: (e: React.DragEvent, id: string) => void;
  onUpdate: (updates: Partial<ExamGroup>) => void;
  onRemove: () => void;
  onPreview: (file: File) => void;
  onRemoveFile: (id: string) => void;
  onToggleFileInclusion: (id: string) => void;
  setFiles: React.Dispatch<React.SetStateAction<FileWithGroup[]>>;
  availableDoctors: Doctor[];
  onAddDoctor: (name: string) => Promise<void>;
  isHovered: boolean;
  onHoverChange: (hovering: boolean) => void;
  isSmartMode: boolean;
  isSingleMode?: boolean;
  categories?: any[];
}> = ({ 
  group, index, files, onDrop, onDragStart, onUpdate, onRemove, 
  onPreview, onRemoveFile, onToggleFileInclusion, setFiles, availableDoctors, onAddDoctor,
  isHovered, onHoverChange, isSmartMode, isSingleMode = false,
  categories = []
}) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

  return (
    <div 
      className={`group relative bg-white dark:bg-dark-surface rounded-[2.5rem] border-2 transition-all overflow-hidden ${
        isHovered 
          ? 'border-blue-500 shadow-2xl shadow-blue-500/10 scale-[1.02]' 
          : 'border-gray-100 dark:border-dark-border shadow-lg'
      } ${isSingleMode ? 'border-none shadow-none !bg-transparent' : ''}`}
      onDragOver={(e) => { e.preventDefault(); !isSingleMode && onHoverChange(true); }}
      onDragLeave={() => !isSingleMode && onHoverChange(false)}
      onDrop={(e) => { !isSingleMode && onHoverChange(false); onDrop(e); }}
    >
      {/* Header */}
      {!isSingleMode && (
        <div className="p-6 pb-4 flex items-start justify-between bg-gradient-to-br from-gray-50/50 to-transparent dark:from-dark-bg/20">
          <div className="flex-1 space-y-1">
            <input 
              type="text" 
              value={group.name} 
              onChange={(e) => onUpdate({ name: e.target.value })}
              className="text-xl font-black text-gray-900 dark:text-dark-text bg-transparent border-none outline-none focus:ring-0 p-0 w-full"
              placeholder="Examination Name"
            />
            <div className="flex flex-wrap gap-3 mt-2">
              {!isSmartMode && (
                <>
                  <div className="flex items-center gap-1.5 px-2.5 py-1 bg-amber-50 dark:bg-amber-900/10 text-amber-700 dark:text-amber-400 rounded-lg border border-amber-100 dark:border-amber-900/20">
                    <Calendar className="w-3.5 h-3.5 flex-shrink-0" />
                    <DatePicker 
                      variant="unstyled"
                      value={group.date}
                      onChange={(date) => onUpdate({ date })}
                      className="bg-transparent border-none outline-none text-[10px] font-black uppercase p-0 focus:ring-0 cursor-pointer min-w-[70px]"
                    />
                  </div>
                  
                  <CategoryDropdown 
                    value={group.category} 
                    onChange={(category) => onUpdate({ category })} 
                    categories={categories}
                  />
                </>
              )}
              {isSmartMode && (
                <div className="flex items-center gap-1.5 px-2.5 py-1 bg-blue-50 dark:bg-blue-900/10 text-blue-700 dark:text-blue-400 rounded-lg border border-blue-100 dark:border-blue-900/20 text-[10px] font-black uppercase tracking-wider">
                  <Sparkles className="w-3 h-3" />
                  <span>Auto-Extracting Details</span>
                </div>
              )}
            </div>
          </div>
          
          <div className="flex items-center gap-2">
            <button 
              type="button"
              onClick={() => setIsExpanded(!isExpanded)}
              className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-all"
            >
              {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
            </button>
            <button 
              type="button"
              onClick={onRemove}
              className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-full transition-all"
            >
              <Trash2 className="w-5 h-5" />
            </button>
          </div>
        </div>
      )}

      {isExpanded && (
        <div className={`${isSingleMode ? 'p-0' : 'px-6 pb-6'} space-y-6 animate-in slide-in-from-top-2 duration-300`}>
          {/* Assigned Files */}
          <div className="space-y-3">
            {!isSingleMode && (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">Assigned Documents ({files.length})</span>
                  <AIBadge workflow="full_reconstruction" size="sm" showText={false} />
                </div>
              </div>
            )}
            
            <div 
              className={`min-h-[120px] p-4 bg-gray-50/50 dark:bg-dark-bg/20 border-2 border-dashed rounded-3xl transition-all ${
                files.length === 0 ? 'border-gray-200 dark:border-dark-border' : 'border-transparent'
              }`}
            >
              {files.length === 0 ? (
                <div className={`flex flex-col ${isMobileDevice() ? 'sm:flex-row' : ''} items-center justify-center gap-6 py-10`}>
                   <button 
                     type="button"
                     className="flex flex-col items-center gap-2 text-gray-400 hover:text-blue-500 transition-colors group/add"
                     onClick={() => fileInputRef.current?.click()}
                   >
                      <div className="p-4 bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border group-hover/add:border-blue-200 transition-all">
                         <Plus className="w-8 h-8" />
                      </div>
                      <span className="text-[10px] font-black uppercase tracking-widest">Add Files</span>
                   </button>
                   
                   {isMobileDevice() && (
                    <button 
                      type="button"
                      className="flex flex-col items-center gap-2 text-gray-400 hover:text-indigo-500 transition-colors group/cam"
                      onClick={() => cameraInputRef.current?.click()}
                    >
                        <div className="p-4 bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border group-hover/cam:border-indigo-200 transition-all">
                          <Camera className="w-8 h-8" />
                        </div>
                        <span className="text-[10px] font-black uppercase tracking-widest">Take Photo</span>
                    </button>
                   )}
                </div>
              ) : (
                <div className="flex flex-wrap gap-3">
                  {files.map(f => (
                    <FileCard 
                      key={f.id} 
                      file={f.file} 
                      onDragStart={(e) => onDragStart(e, f.id)}
                      onRemove={() => onRemoveFile(f.id)}
                      onPreview={() => onPreview(f.file)}
                      onToggleInclusion={() => onToggleFileInclusion(f.id)}
                      includeInExtraction={f.includeInExtraction}
                      draggable={!isSingleMode}
                    />
                  ))}
                  
                  <div
                    onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}
                    className="w-32 h-40 bg-gray-100/50 dark:bg-dark-bg border-2 border-dashed border-gray-200 dark:border-dark-border rounded-2xl flex flex-col items-center justify-center text-gray-400 hover:text-blue-500 hover:border-blue-300 transition-all cursor-pointer group/plus"
                  >
                    <div className="p-3 bg-white/50 dark:bg-dark-surface/50 rounded-full mb-2 group-hover/plus:scale-110 transition-transform">
                      <Plus className="w-6 h-6" />
                    </div>
                    <span className="text-[10px] font-black uppercase tracking-tighter">Add More</span>
                  </div>

                  {isMobileDevice() && (
                    <div
                      onClick={(e) => { e.stopPropagation(); cameraInputRef.current?.click(); }}
                      className="w-32 h-40 bg-indigo-50/50 dark:bg-indigo-900/10 border-2 border-dashed border-indigo-100 dark:border-indigo-900/20 rounded-2xl flex flex-col items-center justify-center text-indigo-400 hover:text-indigo-600 hover:border-indigo-300 transition-all cursor-pointer group/camera"
                    >
                      <div className="p-3 bg-white/50 dark:bg-dark-surface/50 rounded-full mb-2 group-hover/camera:scale-110 transition-transform">
                        <Camera className="w-6 h-6" />
                      </div>
                      <span className="text-[10px] font-black uppercase tracking-tighter">Take Photo</span>
                    </div>
                  )}
                </div>
              )}
              <input 
                type="file" 
                ref={fileInputRef}
                onChange={(e) => {
                  if (e.target.files) {
                    const newFiles = Array.from(e.target.files).map(file => ({
                      id: Math.random().toString(36).substr(2, 9),
                      file,
                      groupId: group.id,
                      includeInExtraction: true
                    }));
                    setFiles(prev => [...prev, ...newFiles]);
                  }
                  e.target.value = '';
                }} 
                multiple 
                className="hidden" 
                accept=".pdf,.jpg,.jpeg,.png,.docx,.txt,.dcm"
              />
              <input 
                type="file" 
                ref={cameraInputRef}
                onChange={(e) => {
                  if (e.target.files) {
                    const newFiles = Array.from(e.target.files).map(file => ({
                      id: Math.random().toString(36).substr(2, 9),
                      file,
                      groupId: group.id,
                      includeInExtraction: true
                    }));
                    setFiles(prev => [...prev, ...newFiles]);
                  }
                  e.target.value = '';
                }} 
                className="hidden" 
                accept="image/*"
                capture="environment"
              />
            </div>
          </div>

          {isSmartMode && isSingleMode && (
            <div className="flex items-center justify-between gap-2 px-4 py-3 bg-blue-50/50 dark:bg-blue-900/10 text-blue-700 dark:text-blue-400 rounded-2xl border border-blue-100 dark:border-blue-900/20 text-xs font-bold animate-in fade-in duration-300">
              <div className="flex items-center gap-1.5">
                <Sparkles className="w-4 h-4" />
                <span>Health Assistant AI will automatically extract examination date, category, doctors, and clinical notes from these documents.</span>
              </div>
              <AIBadge workflow="full_reconstruction" size="sm" showText={false} className="shrink-0" />
            </div>
          )}

          {isSmartMode && !isSingleMode && (
            <div className="space-y-2 mt-4">
              <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">Patient Notes</span>
              <textarea 
                value={group.patientNotes}
                onChange={(e) => onUpdate({ patientNotes: e.target.value })}
                placeholder="How do you feel? Why did you visit the doctor?"
                className="w-full p-4 bg-gray-50/50 dark:bg-dark-bg/20 border border-gray-100 dark:border-dark-border rounded-2xl text-xs outline-none focus:ring-1 focus:ring-blue-500 min-h-[80px]"
              />
            </div>
          )}

          {!isSmartMode && (
            <>
              {/* Doctor Selection */}
              <div className="space-y-2">
                <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">Attending Doctors</span>
                <DoctorSelector 
                  doctors={availableDoctors}
                  selectedIds={group.doctorIds}
                  onSelect={(id) => onUpdate({ doctorIds: [...new Set([...group.doctorIds, id])] })}
                  onDeselect={(id) => onUpdate({ doctorIds: group.doctorIds.filter(i => i !== id) })}
                  onCreateDoctor={onAddDoctor}
                  className="!bg-transparent"
                />
              </div>

              {/* Notes */}
              <div className="space-y-4">
                <div className="space-y-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">Patient Notes</span>
                  <textarea 
                    value={group.patientNotes}
                    onChange={(e) => onUpdate({ patientNotes: e.target.value })}
                    placeholder="How do you feel? Why did you visit the doctor?"
                    className="w-full p-4 bg-gray-50/50 dark:bg-dark-bg/20 border border-gray-100 dark:border-dark-border rounded-2xl text-xs outline-none focus:ring-1 focus:ring-blue-500 min-h-[80px]"
                  />
                </div>
                
                <div className="space-y-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">Clinical Notes</span>
                  <textarea 
                    value={group.notes}
                    onChange={(e) => onUpdate({ notes: e.target.value })}
                    placeholder="Add summary notes for this examination..."
                    className="w-full p-4 bg-gray-50/50 dark:bg-dark-bg/20 border border-gray-100 dark:border-dark-border rounded-2xl text-xs outline-none focus:ring-1 focus:ring-blue-500 min-h-[80px]"
                  />
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

const CategoryDropdown: React.FC<{
  value: string;
  onChange: (value: string) => void;
  categories: any[];
}> = ({ value, onChange, categories }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  
  const filtered = categories.filter(c => c.name.toLowerCase().includes(searchTerm.toLowerCase()));

  return (
    <div className="relative">
      <button 
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 px-2.5 py-1 bg-blue-50 dark:bg-blue-900/10 text-blue-700 dark:text-blue-400 rounded-lg border border-blue-100 dark:border-blue-900/20 text-[10px] font-black uppercase tracking-wider"
      >
        <Tag className="w-3.5 h-3.5" />
        <span>{value}</span>
        <ChevronDown className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-[100]" onClick={() => setIsOpen(false)} />
          <div className="absolute z-[110] mt-2 w-56 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
            <div className="p-2 border-b border-gray-100 dark:border-dark-border">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400" />
                <input 
                  type="text" 
                  value={searchTerm} 
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Search categories..."
                  className="w-full pl-7 pr-3 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-[10px] outline-none"
                  autoFocus
                />
              </div>
            </div>
            <div className="max-h-48 overflow-y-auto">
              {filtered.map(c => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => { onChange(c.name); setIsOpen(false); }}
                  className={`w-full px-4 py-2 text-left text-[10px] font-bold uppercase flex items-center justify-between hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors ${
                    value === c.name ? 'text-blue-600 dark:text-blue-400 bg-blue-50/50 dark:bg-blue-900/10' : 'text-gray-600 dark:text-dark-text'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <div 
                      className="p-1 rounded-md"
                      style={c.color ? { backgroundColor: `${c.color}20`, color: c.color } : {}}
                    >
                      <DynamicIcon icon={c.icon} className="w-3 h-3" />
                    </div>
                    <span>{c.name}</span>
                  </div>
                  {value === c.name && <Check className="w-3 h-3" />}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default ExaminationGroupManager;
