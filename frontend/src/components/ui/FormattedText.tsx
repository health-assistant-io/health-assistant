/**
 * FormattedText — read-only renderer for stored rich-text fields.
 *
 * Detects whether the stored value is HTML (produced by the Quill editor),
 * Markdown (produced by LLMs / AI magic-fill / catalog imports), or plain
 * text, and renders it formatted with prose styling. Used by the catalog
 * item Info tab so description / indications / contraindications / info show
 * as properly formatted paragraphs instead of raw strings.
 */
import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { detectTextFormat } from '../../utils/textFormat';

interface FormattedTextProps {
  value?: string | null;
  className?: string;
}

export const FormattedText: React.FC<FormattedTextProps> = ({ value, className = '' }) => {
  if (!value || !value.trim()) {
    return <span className="text-gray-400">—</span>;
  }

  const format = detectTextFormat(value);

  if (format === 'html') {
    return (
      <div
        className={`prose dark:prose-invert max-w-none prose-sm text-gray-700 dark:text-dark-muted ${className}`}
        dangerouslySetInnerHTML={{ __html: value }}
      />
    );
  }

  if (format === 'markdown') {
    return (
      <div className={`prose dark:prose-invert max-w-none prose-sm text-gray-700 dark:text-dark-muted ${className}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
      </div>
    );
  }

  return <span className={`text-gray-700 dark:text-dark-muted whitespace-pre-wrap ${className}`}>{value}</span>;
};

export default FormattedText;
