import {
  buildChatMarkdown,
  chatExportFileName,
  downloadChatMarkdown,
  type ChatExportMessage,
} from '../ui/chat';
import { getChatSessionMessages } from '../../services/aiAssistanceService';
import type { TaskInfo } from '../../types/ai';

function annotationsFor(tasks: TaskInfo[] | undefined): string[] {
  if (!tasks || tasks.length === 0) return [];
  const resolved = tasks.filter((task) => task.status !== 'proposed');
  if (resolved.length === 0) return [];
  return [
    resolved
      .map((task) => `${task.title || task.task_type}: ${task.status}`)
      .join(' · '),
  ];
}

/** Export one chat session as a Markdown file (career's util pattern). */
export async function exportChatMarkdown(
  sessionId: string,
  title: string,
  assistantLabel: string,
): Promise<void> {
  const rows = await getChatSessionMessages(sessionId);
  const messages: ChatExportMessage[] = rows.map((row) => ({
    role: row.role,
    content: row.content.text,
  }));
  const markdown = buildChatMarkdown(title, messages, {
    assistantLabel,
    annotations: (message) =>
      message.role === 'assistant'
        ? annotationsFor(rows.find((row) => row.content.text === message.content)?.tasks)
        : [],
  });
  downloadChatMarkdown(markdown, chatExportFileName(title));
}
