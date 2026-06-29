import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { 
  Plus, Edit2, Trash2, Save, X, 
  ChevronLeft, LayoutGrid, Palette, Search, Sparkles, Loader2,
  Image as ImageIcon, Upload, Check, ChevronDown
} from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { 
  getExaminationCategories, 
  createExaminationCategory, 
  updateExaminationCategory, 
  deleteExaminationCategory 
} from '../../services/examinationService';
import { getAIAssistance } from '../../services/aiAssistanceService';
import { DynamicIcon, IconConfig } from '../../components/ui/DynamicIcon';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

const COMMON_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', 
  '#ec4899', '#06b6d4', '#f97316', '#6366f1', '#6b7280'
];

// Get all valid Lucide icon names, excluding internal ones and the component itself
const ALL_LUCIDE_ICONS = Object.keys(LucideIcons).filter(key => 
  typeof (LucideIcons as any)[key] === 'function' || (LucideIcons as any)[key].$$typeof
);

const COMMON_ICONS = [
  'Activity', 'ClipboardList', 'Image', 'Droplet', 'TestTube', 
  'Heart', 'Brain', 'Eye', 'Utensils', 'Wind', 'Smile', 
  'Microscope', 'Ear', 'HelpCircle', 'MoreHorizontal', 'Stethoscope'
];

interface CategoryFormData {
  name: string;
  slug: string;
  description: string;
  color: string;
  icon: IconConfig;
}

export function ExaminationCategoryManager() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [categories, setCategories] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isAdding, setIsAdding] = useState(false);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [iconSearchTerm, setIconSearchTerm] = useState('');
  const [iconInstruction, setIconInstruction] = useState('');
  const [referenceImage, setReferenceImage] = useState<string | null>(null);
  const [suggestedIcons, setSuggestedIcons] = useState<string[]>([]);
  const [isSuggesting, setIsSuggesting] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isAiMenuOpen, setIsAiMenuOpen] = useState(false);
  const showConfirmation = useUIStore(state => state.showConfirmation);

  const [formData, setFormData] = useState<CategoryFormData>({
    name: '',
    slug: '',
    description: '',
    color: '#3b82f6',
    icon: { type: 'lucide', value: 'ClipboardList' }
  });

  const filteredIcons = useMemo(() => {
    if (!iconSearchTerm) return COMMON_ICONS;
    const term = iconSearchTerm.toLowerCase();
    return ALL_LUCIDE_ICONS.filter(name => 
      name.toLowerCase().includes(term)
    ).slice(0, 24); // Limit to 24 for performance
  }, [iconSearchTerm]);

  const handleSuggestIcons = async () => {
    if (!formData.name && !formData.description) return;
    
    setIsSuggesting(true);
    try {
      const res = await getAIAssistance({
        task_type: 'suggest_category_icon',
        user_input: `${formData.name} ${formData.description}`
      });
      
      if (res.suggested_icons) {
        // Filter out suggestions that don't exist in our Lucide build
        const validSuggestions = res.suggested_icons.filter((icon: string) => 
          ALL_LUCIDE_ICONS.includes(icon)
        );
        setSuggestedIcons(validSuggestions);
        
        // Auto-select the first valid suggestion if current icon is generic
        if (validSuggestions.length > 0 && formData.icon.type === 'lucide' && (formData.icon.value === 'ClipboardList' || formData.icon.value === 'HelpCircle')) {
          setFormData(prev => ({ ...prev, icon: { type: 'lucide', value: validSuggestions[0] } }));
        }
      }
    } catch (error) {
      console.error("Failed to suggest icons", error);
    } finally {
      setIsSuggesting(false);
    }
  };

  const handleGenerateCustomIcon = async () => {
    if (!formData.name) return;
    
    setIsGenerating(true);
    try {
      const res = await getAIAssistance({
        task_type: 'generate_category_icon',
        user_input: `${formData.name} ${formData.description}`,
        reference_image: referenceImage || undefined,
        context: { 
          instruction: iconInstruction,
          previous_svg: formData.icon.type === 'custom_svg' ? formData.icon.value : null
        }
      });
      
      if (res.svg_content) {
        setFormData(prev => ({ 
          ...prev, 
          icon: { type: 'custom_svg', value: res.svg_content! } 
        }));
      }
    } catch (error) {
      console.error("Failed to generate icon", error);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onloadend = () => {
        setReferenceImage(reader.result as string);
      };
      reader.readAsDataURL(file);
    }
  };

  const handleSvgUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onloadend = () => {
        const content = reader.result as string;
        if (content.includes('<svg')) {
          setFormData({
            ...formData,
            icon: { type: 'custom_svg', value: content }
          });
        }
      };
      reader.readAsText(file);
    }
  };

  const fetchCategories = async () => {
    try {
      const data = await getExaminationCategories();
      setCategories(data);
    } catch (error) {
      console.error("Failed to fetch categories", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCategories();
  }, []);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const parseIcon = (icon: any): IconConfig => {
    if (typeof icon === 'string') {
      return { type: 'lucide', value: icon };
    }
    if (icon && icon.type && icon.value) {
      return icon;
    }
    return { type: 'lucide', value: 'ClipboardList' };
  };

  const handleEdit = (category: any) => {
    setEditingId(category.id);
    setFormData({
      name: category.name,
      slug: category.slug,
      description: category.description || '',
      color: category.color || '#3b82f6',
      icon: parseIcon(category.icon)
    });
    setIconInstruction('');
    setReferenceImage(null);
    setIsAdding(false);
  };

  const handleCancel = () => {
    setEditingId(null);
    setIsAdding(false);
    setIsAiMenuOpen(false);
    setFormData({
      name: '',
      slug: '',
      description: '',
      color: '#3b82f6',
      icon: { type: 'lucide', value: 'ClipboardList' }
    });
    setSuggestedIcons([]);
    setIconInstruction('');
    setReferenceImage(null);
  };

  const handleSave = async () => {
    try {
      if (editingId) {
        await updateExaminationCategory(editingId, formData);
      } else {
        await createExaminationCategory(formData);
      }
      handleCancel();
      fetchCategories();
    } catch (error) {
      console.error("Failed to save category", error);
    }
  };

  const handleDelete = (id: string, name: string) => {
    showConfirmation({
      title: t('common.delete') + ' ' + name,
      message: 'Are you sure you want to delete this category? Examinations using this category will be unlinked.',
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteExaminationCategory(id);
          fetchCategories();
        } catch (error) {
          console.error("Failed to delete category", error);
        }
      }
    });
  };

  const generateSlug = (name: string) => {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  };

  const filteredCategories = categories.filter(c => 
    c.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="max-w-5xl mx-auto pb-10">
      <PageHeader
        title={t('examinations.category_manager.title')}
        subtitle={t('examinations.category_manager.subtitle')}
        icon={<LayoutGrid className="w-8 h-8" />}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <button 
            onClick={() => {
              setIsAdding(true);
              setEditingId(null);
              setFormData({
                name: '',
                slug: '',
                description: '',
                color: '#3b82f6',
                icon: { type: 'lucide', value: 'ClipboardList' }
              });
            }}
            className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95"
          >
            <Plus className="w-4 h-4" />
            <span>{t('examinations.category_manager.new_category')}</span>
          </button>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Category List */}
        <div className="lg:col-span-2 space-y-4">
          <div className="bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
                <thead className="bg-gray-50 dark:bg-dark-bg">
                  <tr>
                    <th className="px-6 py-4 text-left text-xs font-bold text-gray-400 uppercase tracking-widest">Category</th>
                    <th className="px-6 py-4 text-left text-xs font-bold text-gray-400 uppercase tracking-widest">Slug</th>
                    <th className="px-6 py-4 text-right text-xs font-bold text-gray-400 uppercase tracking-widest">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                  {filteredCategories.map((category) => (
                    <tr 
                      key={category.id} 
                      onClick={() => handleEdit(category)}
                      className={`hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-all cursor-pointer group ${
                        editingId === category.id ? 'bg-blue-50/50 dark:bg-blue-900/10 ring-1 ring-inset ring-blue-200 dark:ring-blue-800' : ''
                      }`}
                    >
                      <td className="px-6 py-4">
                        <div className="flex items-center space-x-3">
                          <div 
                            className="p-2 rounded-lg border flex-shrink-0 transition-transform group-hover:scale-110 flex items-center justify-center"
                            style={{ 
                              backgroundColor: `${category.color}20`, 
                              color: category.color,
                              borderColor: `${category.color}40`
                            }}
                          >
                            <DynamicIcon icon={category.icon} className="w-5 h-5" />
                          </div>
                          <div>
                            <div className="text-sm font-bold text-gray-900 dark:text-dark-text">{category.name}</div>
                            {category.description && (
                              <div className="text-xs text-gray-400 line-clamp-1">{category.description}</div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <code className="text-[10px] px-2 py-1 bg-gray-100 dark:bg-dark-bg text-gray-500 rounded font-mono">
                          {category.slug}
                        </code>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex items-center justify-end space-x-2">
                          <button 
                            onClick={(e) => { e.stopPropagation(); handleEdit(category); }}
                            className={`p-2 rounded-lg transition-colors ${
                              editingId === category.id 
                                ? 'bg-blue-600 text-white shadow-md' 
                                : 'text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20'
                            }`}
                          >
                            <Edit2 className="w-4 h-4" />
                          </button>
                          <button 
                            onClick={(e) => { e.stopPropagation(); handleDelete(category.id, category.name); }}
                            disabled={!category.tenant_id && category.slug === 'other'}
                            className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Editor Side Panel */}
        <div className="lg:col-span-1">
          {(editingId || isAdding) ? (
            <div className="bg-white dark:bg-dark-surface rounded-2xl border border-blue-100 dark:border-dark-border p-6 shadow-xl shadow-blue-50/50 sticky top-8 animate-in slide-in-from-right-4 duration-300">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-lg font-bold text-[#1a2b4b] dark:text-dark-text">
                  {editingId ? t('examinations.category_manager.edit_category') : t('examinations.category_manager.new_category')}
                </h2>
                <button onClick={handleCancel} className="text-gray-400 hover:text-gray-600">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="space-y-5">
                <div>
                  <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2">{t('examinations.category_manager.name_label')}</label>
                  <input 
                    type="text"
                    className="w-full px-4 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border dark:bg-dark-bg focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                    value={formData.name}
                    onChange={(e) => {
                      const name = e.target.value;
                      setFormData({ 
                        ...formData, 
                        name, 
                        slug: editingId ? formData.slug : generateSlug(name) 
                      });
                    }}
                    placeholder="e.g. Hematology"
                  />
                </div>

                <div>
                  <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2">{t('examinations.category_manager.slug_label')}</label>
                  <input 
                    type="text"
                    className="w-full px-4 py-2 text-sm rounded-xl border border-gray-100 dark:border-dark-border dark:bg-dark-bg bg-gray-50 text-gray-500 outline-none"
                    value={formData.slug}
                    readOnly
                  />
                </div>

                <div>
                  <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2">Description</label>
                  <textarea 
                    className="w-full px-4 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border dark:bg-dark-bg focus:ring-2 focus:ring-blue-500 outline-none transition-all resize-none h-20 text-sm"
                    value={formData.description}
                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                    placeholder="Describe this clinical specialty..."
                  />
                </div>

                <div>
                  <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2">{t('examinations.category_manager.color_label')}</label>
                  <div className="flex flex-wrap gap-2 mb-3">
                    {COMMON_COLORS.map(color => (
                      <button
                        key={color}
                        onClick={() => setFormData({ ...formData, color })}
                        className={`w-8 h-8 rounded-full transition-transform active:scale-90 ${formData.color === color ? 'ring-2 ring-offset-2 ring-blue-500 scale-110' : ''}`}
                        style={{ backgroundColor: color }}
                      />
                    ))}
                  </div>
                  <input 
                    type="color"
                    className="w-full h-10 p-1 rounded-lg border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-bg"
                    value={formData.color}
                    onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                  />
                </div>

                <div className="space-y-6">
                  {/* Icon Section */}
                  <div className="space-y-4">
                    <label className="block text-xs font-black text-gray-400 uppercase tracking-[0.2em]">{t('examinations.category_manager.icon_label')}</label>
                    
                    {/* Action Buttons Grid */}
                    <div className="grid grid-cols-3 gap-2">
                      <button
                        type="button"
                        onClick={handleSuggestIcons}
                        disabled={isSuggesting || (!formData.name && !formData.description)}
                        className="flex flex-col items-center justify-center gap-1.5 py-3 bg-white dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl hover:border-indigo-200 dark:hover:border-indigo-900/50 hover:bg-indigo-50/30 dark:hover:bg-indigo-900/5 transition-all group disabled:opacity-40 shadow-sm"
                        title="Suggest Lucide icons"
                      >
                        <div className="p-1.5 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded-lg group-hover:scale-110 transition-transform">
                          {isSuggesting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                        </div>
                        <span className="text-[10px] font-black uppercase tracking-tighter text-gray-500 group-hover:text-indigo-600">Suggest</span>
                      </button>

                      <button
                        type="button"
                        onClick={() => setIsAiMenuOpen(!isAiMenuOpen)}
                        disabled={!formData.name}
                        className={`flex flex-col items-center justify-center gap-1.5 py-3 bg-white dark:bg-dark-bg border rounded-xl transition-all group disabled:opacity-40 shadow-sm ${
                          isAiMenuOpen ? 'border-blue-500 ring-1 ring-blue-500/20 bg-blue-50/10' : 'border-gray-100 dark:border-dark-border hover:border-blue-200 dark:hover:border-blue-900/50 hover:bg-blue-50/30'
                        }`}
                      >
                        <div className={`p-1.5 rounded-lg transition-all group-hover:scale-110 ${isAiMenuOpen ? 'bg-blue-600 text-white' : 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400'}`}>
                          <Sparkles className="w-4 h-4" />
                        </div>
                        <span className={`text-[10px] font-black uppercase tracking-tighter transition-colors ${isAiMenuOpen ? 'text-blue-600' : 'text-gray-500'}`}>
                          {formData.icon.type === 'custom_svg' ? t('ai_labels.ai_refine', 'AI Refine') : t('ai_labels.ai_create', 'AI Create')}
                        </span>
                        <ChevronDown className={`w-3 h-3 text-blue-400 transition-transform duration-300 ${isAiMenuOpen ? 'rotate-180' : ''}`} />
                      </button>

                      <label className="flex flex-col items-center justify-center gap-1.5 py-3 bg-white dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl hover:border-emerald-200 dark:hover:border-emerald-900/50 hover:bg-emerald-50/30 dark:hover:bg-emerald-900/5 transition-all group cursor-pointer shadow-sm">
                        <div className="p-1.5 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400 rounded-lg group-hover:scale-110 transition-transform">
                          <Upload className="w-4 h-4" />
                        </div>
                        <span className="text-[10px] font-black uppercase tracking-tighter text-gray-500 group-hover:text-emerald-600">SVG</span>
                        <input type="file" className="hidden" accept=".svg" onChange={handleSvgUpload} />
                      </label>
                    </div>

                    {/* Expandable AI Menu */}
                    {isAiMenuOpen && (
                      <div className="space-y-3 p-4 bg-blue-50/20 dark:bg-blue-900/5 border border-blue-100 dark:border-blue-900/30 rounded-2xl animate-in slide-in-from-top-4 duration-300">
                        {/* Instruction Input */}
                        <div className="relative group">
                          <textarea
                            value={iconInstruction}
                            onChange={(e) => setIconInstruction(e.target.value)}
                            placeholder={formData.icon.type === 'custom_svg' ? "Describe changes to improve icon..." : "Describe how the icon should look..."}
                            className="w-full px-4 py-3 text-[10px] bg-white dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-1 focus:ring-blue-500 outline-none transition-all resize-none h-20 italic text-gray-600 dark:text-dark-text shadow-sm"
                          />
                          <div className="absolute right-3 bottom-3 pointer-events-none opacity-20 group-hover:opacity-100 transition-opacity">
                            <Sparkles className="w-3 h-3 text-blue-500" />
                          </div>
                        </div>

                        {/* Reference Image area */}
                        <div className="flex items-center gap-3">
                          {!referenceImage ? (
                            <label className="flex-1 flex items-center justify-center gap-2 px-3 py-2.5 bg-white dark:bg-dark-bg border border-dashed border-gray-200 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-bg cursor-pointer transition-all group shadow-sm">
                              <ImageIcon className="w-3.5 h-3.5 text-gray-400 group-hover:text-blue-500" />
                              <span className="text-[10px] text-gray-500 font-bold uppercase tracking-tighter">Add Image Guide</span>
                              <input type="file" className="hidden" accept="image/*" onChange={handleImageUpload} />
                            </label>
                          ) : (
                            <div className="flex-1 flex items-center justify-between p-2 bg-white dark:bg-dark-bg border border-blue-200 dark:border-blue-900/50 rounded-xl animate-in fade-in zoom-in-95 duration-200 shadow-sm">
                              <div className="flex items-center gap-2">
                                <img src={referenceImage} alt="Ref" className="w-10 h-10 rounded-lg object-cover border border-gray-100 dark:border-dark-border shadow-sm" />
                                <span className="text-[10px] text-blue-600 dark:text-blue-400 font-black uppercase tracking-tighter">Reference Active</span>
                              </div>
                              <button onClick={() => setReferenceImage(null)} className="p-2 hover:bg-red-50 dark:hover:bg-red-900/10 rounded-lg text-gray-400 hover:text-red-500 transition-all">
                                <X className="w-4 h-4" />
                              </button>
                            </div>
                          )}
                        </div>

                        {/* Final Action Button */}
                        <button
                          type="button"
                          onClick={handleGenerateCustomIcon}
                          disabled={isGenerating || !formData.name}
                          className="w-full flex items-center justify-center gap-2 py-3 bg-blue-600 text-white rounded-xl font-black uppercase tracking-widest text-[11px] hover:bg-blue-700 active:scale-[0.98] transition-all shadow-lg shadow-blue-200 dark:shadow-none disabled:opacity-50"
                        >
                          {isGenerating ? (
                            <>
                              <Loader2 className="w-4 h-4 animate-spin" />
                              <span>Architecting Vector...</span>
                            </>
                          ) : (
                            <>
                              <Sparkles className="w-4 h-4" />
                              <span>{formData.icon.type === 'custom_svg' ? 'Apply Refinements' : 'Generate Icon Now'}</span>
                            </>
                          )}
                        </button>
                      </div>
                    )}

                    {/* Preview Box */}
                    <div className="flex items-center space-x-4 p-5 bg-gradient-to-br from-gray-50 to-white dark:from-dark-bg dark:to-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border shadow-inner">
                      <div className="p-4 bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border relative overflow-hidden">
                        <DynamicIcon icon={formData.icon} className="w-10 h-10" color={formData.color} />
                        <div className="absolute inset-0 rounded-2xl opacity-10" style={{ backgroundColor: formData.color }} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-[9px] font-black text-gray-400 uppercase tracking-[0.2em] mb-1">Selected Style</p>
                        <p className="text-sm font-black text-gray-900 dark:text-dark-text truncate">
                          {formData.icon.type === 'custom_svg' ? 'AI Generated Vector' : `Lucide: ${formData.icon.value}`}
                        </p>
                      </div>
                    </div>

                    {/* AI Suggestions Row */}
                    {suggestedIcons.length > 0 && (
                      <div className="p-3 bg-indigo-50/30 dark:bg-indigo-900/10 border border-indigo-100 dark:border-indigo-900/20 rounded-xl animate-in fade-in slide-in-from-top-2">
                        <p className="text-[9px] font-black text-indigo-500 uppercase tracking-tighter mb-2">Suggestions</p>
                        <div className="flex flex-wrap gap-2">
                          {suggestedIcons.map(iconName => (
                            <button
                              key={`suggest-${iconName}`}
                              onClick={() => setFormData({ ...formData, icon: { type: 'lucide', value: iconName } })}
                              className={`p-2.5 rounded-xl border transition-all flex items-center justify-center ${
                                formData.icon.type === 'lucide' && formData.icon.value === iconName 
                                  ? 'bg-indigo-600 border-indigo-600 text-white shadow-md' 
                                  : 'bg-white dark:bg-dark-surface border-indigo-100 dark:border-indigo-900/30 text-indigo-500 hover:bg-indigo-50'
                              }`}
                              title={iconName}
                            >
                              <DynamicIcon icon={{ type: 'lucide', value: iconName }} className="w-5 h-5" />
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Search and Grid */}
                    <div className="space-y-4 pt-2">
                      <div className="relative">
                        <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                        <input 
                          type="text"
                          placeholder="Search 1400+ icons..."
                          className="w-full pl-10 pr-4 py-2.5 text-xs rounded-xl border border-gray-200 dark:border-dark-border dark:bg-dark-bg focus:ring-2 focus:ring-blue-500/20 outline-none transition-all"
                          value={iconSearchTerm}
                          onChange={(e) => setIconSearchTerm(e.target.value)}
                        />
                      </div>

                      <div className="grid grid-cols-4 gap-2 max-h-56 overflow-y-auto p-1.5 custom-scrollbar bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl border border-gray-100/50 dark:border-dark-border/50">
                        {filteredIcons.map(iconName => (
                          <button
                            key={iconName}
                            onClick={() => setFormData({ ...formData, icon: { type: 'lucide', value: iconName } })}
                            className={`p-3.5 rounded-xl border transition-all flex items-center justify-center relative ${
                              formData.icon.type === 'lucide' && formData.icon.value === iconName 
                                ? 'bg-blue-600 border-blue-600 text-white shadow-lg scale-105 z-10' 
                                : 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border text-gray-400 hover:border-blue-200 hover:text-blue-50'
                            }`}
                          >
                            <DynamicIcon icon={{ type: 'lucide', value: iconName }} className="w-5 h-5" />
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="pt-4">
                  <button 
                    onClick={handleSave}
                    disabled={!formData.name}
                    className="w-full flex items-center justify-center space-x-2 py-3 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all disabled:opacity-50 shadow-lg shadow-blue-100 active:scale-95"
                  >
                    <Save className="w-4 h-4" />
                    <span>{t('examinations.category_manager.save_button')}</span>
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-blue-50/50 dark:bg-blue-900/10 border border-blue-100/50 dark:border-blue-900/20 rounded-2xl p-8 text-center">
              <div className="w-16 h-16 bg-white dark:bg-dark-surface rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-sm">
                <LayoutGrid className="w-8 h-8 text-blue-500" />
              </div>
              <h3 className="font-bold text-gray-900 dark:text-dark-text mb-2">{t('examinations.category_manager.no_selection_title')}</h3>
              <p className="text-sm text-gray-500 dark:text-dark-muted">
                {t('examinations.category_manager.no_selection_desc')}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
