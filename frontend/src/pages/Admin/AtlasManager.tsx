import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Trash2, Upload, Save, X, Loader2, Image as ImageIcon } from 'lucide-react';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyFigure } from '../../types/anatomy';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageContainer } from '../../components/ui/PageContainer';
import { LoadingState } from '../../components/ui/LoadingState';
import { Modal } from '../../components/ui/Modal';
import { useAnatomyAtlas } from '../../components/anatomy/atlas';
import { ImageCropEditor } from '../../components/anatomy/ImageCropEditor';

const EMPTY_FORM = {
  slug: '',
  label: '',
  figure_key: '',
  view_key: '',
  sort_order: 0,
  is_active: true,
};

export const AtlasManager: React.FC = () => {
  const { t } = useTranslation();
  const figures = useAnatomyAtlas((s) => s.figures);
  const load = useAnatomyAtlas((s) => s.load);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<AnatomyFigure | null>(null);
  const [isFormOpen, setIsFormOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      await load(true);
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleDelete = async (slug: string) => {
    if (!confirm(t('anatomy.atlas_confirm_delete', { defaultValue: `Delete figure "${slug}"? Markers keyed to it will be orphaned.` }))) return;
    try {
      await anatomyService.deleteFigure(slug);
      await refresh();
    } catch (e) {
      console.error(e);
    }
  };

  const sorted = Object.values(figures).sort(
    (a, b) => a.figure_key.localeCompare(b.figure_key) || a.sort_order - b.sort_order
  );

  return (
    <>
      <PageHeader
        title={t('anatomy.atlas_manager', { defaultValue: 'Anatomy Atlas' })}
        subtitle={t('anatomy.atlas_subtitle', { defaultValue: 'Manage body figures and views' })}
        icon={<ImageIcon className="w-6 h-6 text-blue-500" />}
      />
      <PageContainer className="px-6 pt-2 pb-6">
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm text-gray-500 dark:text-dark-muted">
            {sorted.length} {t('anatomy.atlas_figures', { defaultValue: 'figures' })}
          </p>
          <button
            onClick={() => { setEditing(null); setIsFormOpen(true); }}
            className="flex items-center gap-1.5 px-3 py-2 bg-blue-500 text-white rounded-xl text-xs font-black uppercase tracking-widest hover:bg-blue-600 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            {t('anatomy.atlas_add', { defaultValue: 'Add Figure' })}
          </button>
        </div>

        {loading ? (
          <LoadingState variant="section" />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {sorted.map((f) => (
              <div
                key={f.slug}
                className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl p-4 flex flex-col"
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="min-w-0">
                    <h3 className="font-bold text-gray-900 dark:text-dark-text truncate">{f.label}</h3>
                    <p className="text-[10px] text-gray-400 font-mono truncate">{f.slug}</p>
                  </div>
                  {!f.is_active && (
                    <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded bg-gray-100 dark:bg-dark-bg text-gray-400">
                      {t('common.inactive', { defaultValue: 'Inactive' })}
                    </span>
                  )}
                </div>
                <FigurePreview slug={f.slug} className="h-40 mb-3" />
                <div className="text-[10px] text-gray-400 flex flex-wrap gap-x-3 gap-y-0.5 mb-3">
                  <span>group: <b className="text-gray-600 dark:text-dark-muted">{f.figure_key}</b></span>
                  <span>view: <b className="text-gray-600 dark:text-dark-muted">{f.view_key}</b></span>
                  <span>order: <b className="text-gray-600 dark:text-dark-muted">{f.sort_order}</b></span>
                  <span>dims: <b className="text-gray-600 dark:text-dark-muted font-mono">{f.width}×{f.height}</b></span>
                </div>
                <div className="flex gap-2 mt-auto">
                  <button
                    onClick={() => { setEditing(f); setIsFormOpen(true); }}
                    className="flex-1 flex items-center justify-center gap-1 px-2 py-1.5 bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted rounded-lg text-[10px] font-black uppercase hover:bg-gray-200 dark:hover:bg-dark-border transition-colors"
                  >
                    <Upload className="w-3 h-3" /> {t('common.edit', { defaultValue: 'Edit' })}
                  </button>
                  <button
                    onClick={() => handleDelete(f.slug)}
                    className="flex items-center justify-center gap-1 px-2 py-1.5 bg-red-50 dark:bg-red-900/20 text-red-500 rounded-lg text-[10px] font-black uppercase hover:bg-red-100 dark:hover:bg-red-900/40 transition-colors"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </PageContainer>

      <FigureForm
        isOpen={isFormOpen}
        onClose={() => setIsFormOpen(false)}
        figure={editing}
        onSaved={async () => { setIsFormOpen(false); await refresh(); }}
      />
    </>
  );
};

const FigurePreview: React.FC<{ slug: string; className?: string }> = ({ slug, className }) => {
  const imageUrl = useAnatomyAtlas((s) => s.imageUrls[slug]);
  const getImage = useAnatomyAtlas((s) => s.getImage);
  const figures = useAnatomyAtlas((s) => s.figures);
  useEffect(() => {
    if (figures[slug] && !imageUrl) getImage(slug);
  }, [slug, figures, imageUrl, getImage]);
  return (
    <div className={`bg-gray-50 dark:bg-dark-bg rounded-xl flex items-center justify-center overflow-hidden ${className}`}>
      {imageUrl ? (
        <img src={imageUrl} alt={slug} className="max-h-full max-w-full object-contain" />
      ) : (
        <ImageIcon className="w-8 h-8 text-gray-200" />
      )}
    </div>
  );
};

interface FigureFormProps {
  isOpen: boolean;
  onClose: () => void;
  figure: AnatomyFigure | null;
  onSaved: () => void;
}

const FigureForm: React.FC<FigureFormProps> = ({ isOpen, onClose, figure, onSaved }) => {
  const { t } = useTranslation();
  const allFigures = useAnatomyAtlas((s) => s.figures);
  const [form, setForm] = useState(EMPTY_FORM);
  const [sourceUrl, setSourceUrl] = useState('');       // image shown in the crop editor
  const [croppedBlob, setCroppedBlob] = useState<Blob | null>(null);
  const [newSourceFile, setNewSourceFile] = useState<File | null>(null); // original upload (to send as source)
  const [currentImageUrl, setCurrentImageUrl] = useState('');            // existing cropped image preview (edit mode)
  const [cropMode, setCropMode] = useState<'none' | 'new' | 'original' | 'current'>('none');
  const [figureKeyNew, setFigureKeyNew] = useState(false);
  const [viewKeyNew, setViewKeyNew] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isEdit = !!figure;
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Derive existing groups / views from all figures for the dropdowns.
  const existingGroups = useMemo(
    () => Array.from(new Set(Object.values(allFigures).map((f) => f.figure_key))).sort(),
    [allFigures],
  );
  const existingViews = useMemo(
    () => Array.from(new Set(Object.values(allFigures).map((f) => f.view_key))).sort(),
    [allFigures],
  );

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setCroppedBlob(null);
    setSourceUrl('');
    setNewSourceFile(null);
    setCropMode('none');
    setCurrentImageUrl('');
    setFigureKeyNew(false);
    setViewKeyNew(false);
    if (figure) {
      setForm({
        slug: figure.slug,
        label: figure.label,
        figure_key: figure.figure_key,
        view_key: figure.view_key,
        sort_order: figure.sort_order,
        is_active: figure.is_active,
      });
      // Load the current cropped image for preview.
      anatomyService.fetchFigureImage(figure.slug).then((url) => url && setCurrentImageUrl(url));
    } else {
      setForm(EMPTY_FORM);
    }
  }, [isOpen, figure]);

  // Revoke object URL on cleanup / source change.
  useEffect(() => {
    return () => { if (sourceUrl.startsWith('blob:')) URL.revokeObjectURL(sourceUrl); };
  }, [sourceUrl]);

  useEffect(() => {
    return () => { if (currentImageUrl.startsWith('blob:')) URL.revokeObjectURL(currentImageUrl); };
  }, [currentImageUrl]);

  const onFile = (file: File) => {
    if (sourceUrl.startsWith('blob:')) URL.revokeObjectURL(sourceUrl);
    setSourceUrl(URL.createObjectURL(file));
    setNewSourceFile(file);
    setCroppedBlob(null);
    setCropMode('new');
  };

  const recropOriginal = async () => {
    if (!figure) return;
    const url = await anatomyService.fetchFigureSourceImage(figure.slug);
    if (url) {
      if (sourceUrl.startsWith('blob:')) URL.revokeObjectURL(sourceUrl);
      setSourceUrl(url);
      setNewSourceFile(null);
      setCroppedBlob(null);
      setCropMode('original');
    }
  };

  const recropCurrent = async () => {
    if (!figure) return;
    const url = await anatomyService.fetchFigureImage(figure.slug);
    if (url) {
      if (sourceUrl.startsWith('blob:')) URL.revokeObjectURL(sourceUrl);
      setSourceUrl(url);
      setNewSourceFile(null);
      setCroppedBlob(null);
      setCropMode('current');
    }
  };

  const submit = async () => {
    if (!form.label || !form.figure_key || !form.view_key) {
      setError(t('anatomy.atlas_error_fields', { defaultValue: 'Label, figure group, and view are required.' }));
      return;
    }
    const slug = form.slug || `${form.figure_key}-${form.view_key}`;
    setSaving(true);
    setError(null);
    try {
      if (isEdit) {
        await anatomyService.updateFigure(figure!.slug, {
          label: form.label,
          figure_key: form.figure_key,
          view_key: form.view_key,
          sort_order: Number(form.sort_order),
          is_active: form.is_active,
          ...(croppedBlob ? { image: croppedBlob } : {}),
          ...(newSourceFile ? { source: newSourceFile } : {}),
        });
      } else {
        if (!croppedBlob) {
          setError(t('anatomy.atlas_error_crop', { defaultValue: 'Upload a source image and select the crop region.' }));
          setSaving(false);
          return;
        }
        await anatomyService.createFigure({
          slug,
          label: form.label,
          figure_key: form.figure_key,
          view_key: form.view_key,
          image: croppedBlob,
          // Keep the original upload for future re-cropping.
          source: newSourceFile,
          sort_order: Number(form.sort_order),
          is_active: form.is_active,
        });
      }
      onSaved();
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (Array.isArray(detail)) {
        setError(detail.map((d: any) => d.msg).join('; '));
      } else if (typeof detail === 'string') {
        setError(detail);
      } else {
        setError(e?.message ?? 'Save failed');
      }
    } finally {
      setSaving(false);
    }
  };

  const field = "w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text";
  const labelCls = "text-[10px] font-black uppercase text-gray-400 tracking-widest mb-1 block";

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={isEdit ? t('anatomy.atlas_edit', { defaultValue: 'Edit Figure' }) : t('anatomy.atlas_add', { defaultValue: 'Add Figure' })} className="max-w-3xl">
      <div className="space-y-4 max-h-[70vh] overflow-y-auto custom-scrollbar pr-1">
        {error && <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 text-sm rounded-xl px-3 py-2">{error}</div>}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>{t('anatomy.atlas_label', { defaultValue: 'Label' })}</label>
            <input className={field} value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} placeholder="Male — Front" />
          </div>
          {!isEdit && (
            <div>
              <label className={labelCls}>{t('anatomy.atlas_slug', { defaultValue: 'Slug (optional)' })}</label>
              <input className={field} value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="man-front" />
            </div>
          )}
          <div>
            <label className={labelCls}>{t('anatomy.atlas_figure_key', { defaultValue: 'Figure group' })}</label>
            {figureKeyNew ? (
              <div className="flex gap-2">
                <input className={field} value={form.figure_key} onChange={(e) => setForm({ ...form, figure_key: e.target.value })} placeholder="new-group" autoFocus />
                <button type="button" onClick={() => { setFigureKeyNew(false); setForm({ ...form, figure_key: existingGroups[0] ?? '' }); }} className="px-2 text-[10px] text-gray-400 hover:text-gray-600">list</button>
              </div>
            ) : (
              <div className="flex gap-2">
                <select className={field} value={form.figure_key} onChange={(e) => { if (e.target.value === '__new__') { setFigureKeyNew(true); setForm({ ...form, figure_key: '' }); } else setForm({ ...form, figure_key: e.target.value }); }}>
                  <option value="" disabled>Select…</option>
                  {existingGroups.map((g) => <option key={g} value={g}>{g}</option>)}
                  <option value="__new__">+ New group…</option>
                </select>
                {!existingGroups.includes(form.figure_key) && form.figure_key && (
                  <button type="button" onClick={() => setFigureKeyNew(true)} className="px-2 text-[10px] text-gray-400 hover:text-gray-600">edit</button>
                )}
              </div>
            )}
          </div>
          <div>
            <label className={labelCls}>{t('anatomy.atlas_view_key', { defaultValue: 'View' })}</label>
            {viewKeyNew ? (
              <div className="flex gap-2">
                <input className={field} value={form.view_key} onChange={(e) => setForm({ ...form, view_key: e.target.value })} placeholder="new-view" autoFocus />
                <button type="button" onClick={() => { setViewKeyNew(false); setForm({ ...form, view_key: existingViews[0] ?? '' }); }} className="px-2 text-[10px] text-gray-400 hover:text-gray-600">list</button>
              </div>
            ) : (
              <div className="flex gap-2">
                <select className={field} value={form.view_key} onChange={(e) => { if (e.target.value === '__new__') { setViewKeyNew(true); setForm({ ...form, view_key: '' }); } else setForm({ ...form, view_key: e.target.value }); }}>
                  <option value="" disabled>Select…</option>
                  {existingViews.map((v) => <option key={v} value={v}>{v}</option>)}
                  <option value="__new__">+ New view…</option>
                </select>
                {!existingViews.includes(form.view_key) && form.view_key && (
                  <button type="button" onClick={() => setViewKeyNew(true)} className="px-2 text-[10px] text-gray-400 hover:text-gray-600">edit</button>
                )}
              </div>
            )}
          </div>
        </div>

        <div>
          <label className={labelCls}>
            {isEdit
              ? t('anatomy.atlas_replace_image', { defaultValue: 'Image (upload new, re-crop current, or re-crop original)' })
              : t('anatomy.atlas_source_image', { defaultValue: 'Source image (upload + crop)' })}
          </label>
          {/* Current image preview (edit mode only) */}
          {isEdit && currentImageUrl && !sourceUrl && (
            <div className="mb-3 flex items-center gap-3">
              <div className="bg-gray-50 dark:bg-dark-bg rounded-xl p-1 flex-shrink-0">
                <img src={currentImageUrl} alt="current" className="h-20 w-auto object-contain" />
              </div>
              <span className="text-[10px] text-gray-400">Current image (saved crop)</span>
            </div>
          )}
          <div className="flex gap-2 mb-2 flex-wrap items-center">
            <input ref={fileInputRef} type="file" accept="image/png,image/webp,image/jpeg" className="hidden" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
            {sourceUrl ? (
              <span className="text-[10px] text-gray-400">
                {cropMode === 'original' ? 'original loaded — drag to re-crop'
                  : cropMode === 'current' ? 'current image loaded — drag to trim'
                  : cropMode === 'new' ? (croppedBlob ? `${(croppedBlob.size / 1024).toFixed(1)} KB cropped` : 'new image loaded — drag to crop')
                  : 'drag the selection to crop'}
              </span>
            ) : (
              <select
                value=""
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === 'upload') fileInputRef.current?.click();
                  else if (v === 'current') recropCurrent();
                  else if (v === 'original') recropOriginal();
                }}
                className="px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-xs font-bold outline-none cursor-pointer dark:text-dark-text"
              >
                <option value="" disabled>{t('anatomy.atlas_crop_actions', { defaultValue: 'Choose image source…' })}</option>
                <option value="upload">{t('anatomy.atlas_upload', { defaultValue: 'Upload new image…' })}</option>
                {isEdit && (
                  <option value="current">{t('anatomy.atlas_recrop_current', { defaultValue: 'Re-crop current image' })}</option>
                )}
                {isEdit && (
                  <option value="original" disabled={!figure?.source_image_path}>
                    {t('anatomy.atlas_use_original', { defaultValue: 'Re-crop from original' })}{figure?.source_image_path ? '' : ' (none stored)'}
                  </option>
                )}
              </select>
            )}
          </div>
          <ImageCropEditor sourceUrl={sourceUrl} onCropChange={setCroppedBlob} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div><label className={labelCls}>{t('anatomy.atlas_sort', { defaultValue: 'Sort order' })}</label><input type="number" className={field} value={form.sort_order} onChange={(e) => setForm({ ...form, sort_order: Number(e.target.value) })} /></div>
          <div className="flex items-end pb-1">
            <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-dark-muted cursor-pointer">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
              {t('common.active', { defaultValue: 'Active' })}
            </label>
          </div>
        </div>
      </div>

      <div className="flex justify-end gap-2 mt-4">
        <button onClick={onClose} className="flex items-center gap-1.5 px-4 py-2 text-gray-500 rounded-xl text-xs font-black uppercase hover:bg-gray-100 dark:hover:bg-dark-bg transition-colors">
          <X className="w-3.5 h-3.5" /> {t('common.cancel', { defaultValue: 'Cancel' })}
        </button>
        <button onClick={submit} disabled={saving} className="flex items-center gap-1.5 px-4 py-2 bg-blue-500 text-white rounded-xl text-xs font-black uppercase hover:bg-blue-600 transition-colors disabled:opacity-50">
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          {t('common.save', { defaultValue: 'Save' })}
        </button>
      </div>
    </Modal>
  );
};

export default AtlasManager;
