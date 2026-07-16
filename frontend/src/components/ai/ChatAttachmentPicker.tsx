import React, { useRef, useState } from 'react';
import { Paperclip, X, ImageIcon, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  ChatAttachmentStatus,
  PendingChatAttachment,
} from '../../types/ai';
import {
  CHAT_IMAGE_CONSTRAINTS,
  useChatAttachments,
} from '../../hooks/useChatAttachments';
import { ImageViewer } from '../ui/ImageViewer';

interface Props {
  attachments: PendingChatAttachment[];
  onChange: (next: PendingChatAttachment[]) => void;
  onToast?: (message: string) => void;
  disabled?: boolean;
  /** Render variant: a compact icon button (drawer) vs a labelled pill. */
  variant?: 'icon' | 'pill';
}

/**
 * Composer control for attaching images to a chat message.
 *
 * Handles three input vectors — click-to-pick, drag-and-drop onto the input
 * bar, and clipboard paste — all routed through the same validated pipeline
 * (:hook:`useChatAttachments`). Renders a horizontal preview rail of pending
 * thumbnails with remove buttons above the text input.
 *
 * Validation (MIME + size + count) happens client-side so rejected files are
 * never base64-encoded or shipped to the server; the backend re-validates at
 * the trust boundary.
 */
export const ChatAttachmentPicker: React.FC<Props> = ({
  attachments,
  onChange,
  onToast,
  disabled,
  variant = 'icon',
}) => {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const { addFiles } = useChatAttachments(onToast);

  const handleSelect = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const next = await addFiles(files, attachments);
    onChange(next);
  };

  const remaining = CHAT_IMAGE_CONSTRAINTS.maxImages - attachments.length;

  return (
    <div className="flex items-center">
      <input
        ref={inputRef}
        type="file"
        accept={CHAT_IMAGE_CONSTRAINTS.acceptAttribute}
        multiple
        className="hidden"
        onChange={(e) => {
          handleSelect(e.target.files);
          // Reset so the same file can be re-selected after removal.
          e.target.value = '';
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || remaining <= 0}
        title={t('ai_chat.attachments.add', { defaultValue: 'Attach images' })}
        className={
          variant === 'pill'
            ? 'flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-bold text-gray-500 dark:text-dark-muted hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-dark-bg transition-all disabled:opacity-40 disabled:cursor-not-allowed'
            : 'p-2 rounded-xl text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-dark-bg transition-all disabled:opacity-40 disabled:cursor-not-allowed'
        }
      >
        <Paperclip className="w-4 h-4 md:w-[18px] md:h-[18px]" />
        {variant === 'pill' && (
          <span className="hidden sm:inline">
            {t('ai_chat.attachments.add', { defaultValue: 'Images' })}
          </span>
        )}
      </button>
    </div>
  );
};

interface PreviewRailProps {
  attachments: PendingChatAttachment[];
  onRemove: (id: string) => void;
}

/**
 * Horizontal thumbnail rail shown above the text input while attachments are
 * staged (not yet sent). Each thumbnail shows an encoding spinner, a remove
 * button, and — once encoded — opens a full-screen lightbox on click so the
 * user can preview the image at full size before sending.
 *
 * Lives OUTSIDE the rounded input box so thumbnails are never clipped by the
 * box's ``overflow-hidden`` + ``rounded`` corners.
 */
export const ChatAttachmentPreviewRail: React.FC<PreviewRailProps> = ({
  attachments,
  onRemove,
}) => {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  if (attachments.length === 0) return null;

  // Only attachments that have finished encoding have a viewable data URL.
  const viewable = attachments
    .map((a, i) => ({ a, i }))
    .filter(({ a }) => a.dataUrl && a.status === ChatAttachmentStatus.Ready);

  const openLightboxFor = (attachmentId: string) => {
    const idx = viewable.findIndex(({ a }) => a.id === attachmentId);
    if (idx !== -1) setLightboxIndex(idx);
  };

  return (
    <>
      <div className="flex flex-wrap gap-2 px-1 pb-2">
        {attachments.map((att) => (
          <AttachmentThumb
            key={att.id}
            attachment={att}
            onRemove={onRemove}
            onOpen={() => att.dataUrl && openLightboxFor(att.id)}
          />
        ))}
      </div>
      {lightboxIndex !== null && (
        <>
          <ImageViewer
            key={lightboxIndex}
            url={viewable[lightboxIndex].a.dataUrl as string}
            filename={viewable[lightboxIndex].a.name || `Attachment ${lightboxIndex + 1}`}
            editable={false}
            onClose={() => setLightboxIndex(null)}
          />
          {viewable.length > 1 && (
            <>
              <NavArrow
                side="left"
                onClick={() =>
                  setLightboxIndex((i) =>
                    i === null ? i : (i - 1 + viewable.length) % viewable.length,
                  )
                }
              />
              <NavArrow
                side="right"
                onClick={() =>
                  setLightboxIndex((i) =>
                    i === null ? i : (i + 1) % viewable.length,
                  )
                }
              />
            </>
          )}
        </>
      )}
    </>
  );
};

/** Floating prev/next arrow above the ImageViewer for multi-image preview. */
const NavArrow: React.FC<{ side: 'left' | 'right'; onClick: () => void }> = ({
  side,
  onClick,
}) => (
  <button
    type="button"
    onClick={onClick}
    className={`fixed top-1/2 -translate-y-1/2 z-[1001] p-3 rounded-full bg-white/10 hover:bg-white/25 text-white backdrop-blur-sm transition-all active:scale-90 ${
      side === 'left' ? 'left-4' : 'right-4'
    }`}
    title={side === 'left' ? 'Previous image' : 'Next image'}
  >
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d={side === 'left' ? 'M15 19l-7-7 7-7' : 'M9 5l7 7-7 7'}
      />
    </svg>
  </button>
);

const AttachmentThumb: React.FC<{
  attachment: PendingChatAttachment;
  onRemove: (id: string) => void;
  onOpen?: () => void;
}> = ({ attachment, onRemove, onOpen }) => {
  const isEncoding = attachment.status === ChatAttachmentStatus.Encoding;
  const ready = !!attachment.dataUrl && !isEncoding;
  return (
    <div
      className={`group relative w-16 h-16 rounded-xl overflow-hidden border border-gray-200 dark:border-dark-border bg-gray-100 dark:bg-dark-bg shadow-sm ${
        ready ? 'cursor-pointer' : ''
      }`}
      onClick={ready ? onOpen : undefined}
      role={ready ? 'button' : undefined}
    >
      {attachment.dataUrl && !isEncoding ? (
        <img
          src={attachment.dataUrl}
          alt={attachment.name}
          className="w-full h-full object-cover transition-transform duration-200 group-hover:scale-110"
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-gray-400 dark:text-dark-muted">
          {isEncoding ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <ImageIcon className="w-5 h-5" />
          )}
        </div>
      )}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onRemove(attachment.id);
        }}
        className="absolute top-0.5 right-0.5 w-4 h-4 rounded-full bg-black/60 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
        title="Remove"
      >
        <X className="w-2.5 h-2.5" />
      </button>
    </div>
  );
};
