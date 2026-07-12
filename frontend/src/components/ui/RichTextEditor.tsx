import React, { useMemo } from 'react';
import ReactQuill from 'react-quill-new';
import 'react-quill-new/dist/quill.snow.css';
import { marked } from 'marked';

interface RichTextEditorProps {
  value: string;
  onChange: (content: string) => void;
  placeholder?: string;
  minHeight?: string;
  className?: string;
  /** Compact toolbar (header, bold/italic, lists, link) — for modal forms
   *  where the full toolbar (fonts, colors, scripts, video…) is overkill. */
  compact?: boolean;
}

const fullModules = {
  toolbar: [
    [{ 'header': [1, 2, 3, 4, 5, 6, false] }],
    [{ 'font': [] }],
    [{ 'size': ['small', false, 'large', 'huge'] }],
    ['bold', 'italic', 'underline', 'strike'],
    [{ 'color': [] }, { 'background': [] }],
    [{ 'script': 'sub' }, { 'script': 'super' }],
    ['blockquote', 'code-block'],
    [{ 'list': 'ordered' }, { 'list': 'bullet' }, { 'indent': '-1' }, { 'indent': '+1' }],
    [{ 'direction': 'rtl' }, { 'align': [] }],
    ['link', 'image', 'video'],
    ['clean']
  ],
};

const compactModules = {
  toolbar: [
    [{ 'header': [1, 2, 3, false] }],
    ['bold', 'italic', 'underline'],
    [{ 'list': 'ordered' }, { 'list': 'bullet' }],
    ['link', 'clean'],
  ],
};

const fullFormats = [
  'header', 'font', 'size',
  'bold', 'italic', 'underline', 'strike',
  'color', 'background',
  'script', 'blockquote', 'code-block',
  'list', 'indent',
  'direction', 'align',
  'link', 'image', 'video'
];

const compactFormats = [
  'header',
  'bold', 'italic', 'underline',
  'list', 'bullet',
  'link',
];

export const RichTextEditor: React.FC<RichTextEditorProps> = ({
  value,
  onChange,
  placeholder = 'Write something...',
  minHeight = '250px',
  className = '',
  compact = false,
}) => {
  // Convert Markdown to HTML if it doesn't look like HTML
  const content = useMemo(() => {
    if (!value) return '';
    const isHtml = /<\/?[a-z][\s\S]*>/i.test(value);
    if (!isHtml) {
      // It's likely Markdown, convert to HTML for the editor
      return marked.parse(value) as string;
    }
    return value;
  }, [value]);

  return (
    <div className={`modern-quill-wrapper rounded-[2rem] border border-gray-200 dark:border-dark-border overflow-hidden bg-white dark:bg-dark-surface shadow-inner ${className}`}>
      <style>{`
        .ql-container { min-height: ${minHeight}; font-family: inherit; font-size: 1rem; }
        .ql-editor { padding: 24px; line-height: 1.8; }
        .ql-editor.ql-blank::before { color: #94a3b8; font-style: normal; left: 24px; }
        
        /* Dark Mode Quill Overrides */
        .dark .ql-toolbar.ql-snow {
          border-color: #334155;
          background-color: #1e293b;
        }
        .dark .ql-container.ql-snow {
          border-color: #334155;
          background-color: #0f172a;
          color: #f1f5f9;
        }
        .dark .ql-snow .ql-stroke {
          stroke: #94a3b8;
        }
        .dark .ql-snow .ql-fill {
          fill: #94a3b8;
        }
        .dark .ql-snow .ql-picker {
          color: #94a3b8;
        }
        .dark .ql-snow .ql-picker-options {
          background-color: #1e293b;
          border-color: #334155;
          box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
          z-index: 1000;
        }
        .dark .ql-snow .ql-picker-item {
          color: #94a3b8;
        }
        .dark .ql-snow .ql-color-picker .ql-picker-item {
          border: 1px solid #334155;
        }
        .dark .ql-snow .ql-color-picker .ql-picker-options {
          padding: 8px;
        }
        .dark .ql-snow .ql-tooltip {
          background-color: #1e293b;
          border-color: #334155;
          color: #f1f5f9;
          box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
          z-index: 1001;
        }
        .dark .ql-snow .ql-tooltip input[type=text] {
          background-color: #0f172a;
          border-color: #334155;
          color: #f1f5f9;
        }
        .dark .ql-snow.ql-toolbar button:hover,
        .dark .ql-snow.ql-toolbar button:focus,
        .dark .ql-snow.ql-toolbar button.ql-active,
        .dark .ql-snow.ql-toolbar .ql-picker-label:hover,
        .dark .ql-snow.ql-toolbar .ql-picker-label.ql-active,
        .dark .ql-snow.ql-toolbar .ql-picker-item:hover,
        .dark .ql-snow.ql-toolbar .ql-picker-item.ql-selected {
          color: #3b82f6;
        }
        .dark .ql-snow.ql-toolbar button:hover .ql-stroke,
        .dark .ql-snow.ql-toolbar button:focus .ql-stroke,
        .dark .ql-snow.ql-toolbar button.ql-active .ql-stroke,
        .dark .ql-snow.ql-toolbar .ql-picker-label:hover .ql-stroke,
        .dark .ql-snow.ql-toolbar .ql-picker-label.ql-active .ql-stroke,
        .dark .ql-snow.ql-toolbar .ql-picker-item:hover .ql-stroke,
        .dark .ql-snow.ql-toolbar .ql-picker-item.ql-selected .ql-stroke {
          stroke: #3b82f6;
        }
        .dark .ql-snow.ql-toolbar button:hover .ql-fill,
        .dark .ql-snow.ql-toolbar button:focus .ql-fill,
        .dark .ql-snow.ql-toolbar button.ql-active .ql-fill,
        .dark .ql-snow.ql-toolbar .ql-picker-label:hover .ql-fill,
        .dark .ql-snow.ql-toolbar .ql-picker-label.ql-active .ql-fill,
        .dark .ql-snow.ql-toolbar .ql-picker-item:hover .ql-fill,
        .dark .ql-snow.ql-toolbar .ql-picker-item.ql-selected .ql-fill {
          fill: #3b82f6;
        }
      `}</style>
      <ReactQuill
        theme="snow"
        value={content}
        onChange={onChange}
        modules={compact ? compactModules : fullModules}
        formats={compact ? compactFormats : fullFormats}
        placeholder={placeholder}
      />
    </div>
  );
};
