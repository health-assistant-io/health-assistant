import { useCallback } from 'react';
import { ChatAttachmentStatus, PendingChatAttachment } from '../types/ai';

/**
 * Client-side constraints for chat image attachments. These mirror the
 * backend limits (``AI_CHAT_MAX_IMAGES`` / ``AI_CHAT_MAX_IMAGE_BYTES`` in
 * ``app/core/config.py``) and validate early so a rejected file never gets
 * base64-encoded or shipped to the server.
 */
export const CHAT_IMAGE_CONSTRAINTS = {
  maxImages: 4,
  maxImageBytes: 8 * 1024 * 1024,
  /** MIME types accepted by the vision backend (see AllowedImageMime). */
  acceptedMimeTypes: ['image/jpeg', 'image/png', 'image/webp', 'image/gif'] as string[],
  /** Accept string for ``<input type="file">``. */
  acceptAttribute: 'image/jpeg,image/png,image/webp,image/gif',
};

let attachmentIdCounter = 0;
const nextAttachmentId = () => `att-${Date.now()}-${++attachmentIdCounter}`;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Read a File into an RFC 2397 data URL. */
function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
}

/** Validate a single File against the client constraints. Returns an error
 * message string, or ``null`` when the file is acceptable. */
export function validateImageFile(file: File): string | null {
  if (!CHAT_IMAGE_CONSTRAINTS.acceptedMimeTypes.includes(file.type)) {
    return `Unsupported type. Allowed: JPG, PNG, WEBP, GIF.`;
  }
  if (file.size > CHAT_IMAGE_CONSTRAINTS.maxImageBytes) {
    return `Too large (${formatBytes(file.size)}). Max ${formatBytes(
      CHAT_IMAGE_CONSTRAINTS.maxImageBytes,
    )}.`;
  }
  return null;
}

interface UseChatAttachmentsResult {
  /** Add files from a picker, drop, or paste. Returns the resulting pending
   * attachments so the caller can merge them into state. Respects the max
   * count by dropping overflow with a "rejected" marker. */
  addFiles: (
    files: FileList | File[],
    current: PendingChatAttachment[],
  ) => Promise<PendingChatAttachment[]>;
}

export function useChatAttachments(
  onToast?: (message: string) => void,
): UseChatAttachmentsResult {
  const addFiles = useCallback(
    async (files: FileList | File[], current: PendingChatAttachment[]) => {
      const incoming = Array.from(files);
      const results: PendingChatAttachment[] = [...current];

      for (const file of incoming) {
        const remainingSlots = CHAT_IMAGE_CONSTRAINTS.maxImages - results.length;
        if (remainingSlots <= 0) {
          onToast?.(
            `You can attach up to ${CHAT_IMAGE_CONSTRAINTS.maxImages} images per message.`,
          );
          break;
        }

        const validationError = validateImageFile(file);
        if (validationError) {
          onToast?.(`${file.name}: ${validationError}`);
          continue;
        }

        const encoding: PendingChatAttachment = {
          id: nextAttachmentId(),
          name: file.name,
          size: file.size,
          mimeType: file.type,
          status: ChatAttachmentStatus.Encoding,
        };
        results.push(encoding);
        try {
          const dataUrl = await readFileAsDataUrl(file);
          const idx = results.findIndex((a) => a.id === encoding.id);
          if (idx !== -1) {
            results[idx] = {
              ...encoding,
              dataUrl,
              status: ChatAttachmentStatus.Ready,
            };
          }
        } catch {
          const idx = results.findIndex((a) => a.id === encoding.id);
          if (idx !== -1) {
            results.splice(idx, 1);
          }
          onToast?.(`${file.name}: could not read file.`);
        }
      }
      return results;
    },
    [onToast],
  );

  return { addFiles };
}

export { formatBytes };
