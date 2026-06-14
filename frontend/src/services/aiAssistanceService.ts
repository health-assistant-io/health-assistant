import api from '../api/axios';

export interface AIAssistanceRequest {
  task_type: 'fill_biomarker_form' | 'fill_medication_form' | 'define_biomarker' | 'define_medication' | 'chat' | 'magic_fill_examination' | 'suggest_category_icon' | 'generate_category_icon';
  user_input: string;
  reference_image?: string;
  context?: Record<string, any>;
}

export interface AIAssistanceResponse {
  task_type: string;
  suggested_data?: Record<string, any>;
  suggested_icons?: string[];
  svg_content?: string;
  justification?: string;
  message?: string;
  session_id?: string;
  success: boolean;
  error?: string;
}

export interface ChatSession {
  id: string;
  title?: string;
  patient_id?: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: { text: string };
  tool_calls?: any[];
  citations?: string[];
  created_at: string;
}

export const getAIAssistance = async (request: AIAssistanceRequest): Promise<AIAssistanceResponse> => {
  const response = await api.post<AIAssistanceResponse>('/ai-assistance/assist', request);
  return response.data;
};

export const listChatSessions = async (patientId?: string): Promise<ChatSession[]> => {
  const response = await api.get<ChatSession[]>('/ai-assistance/sessions', {
    params: { patient_id: patientId }
  });
  return response.data;
};

export const getChatSessionMessages = async (sessionId: string): Promise<ChatMessage[]> => {
  const response = await api.get<ChatMessage[]>(`/ai-assistance/sessions/${sessionId}/messages`);
  return response.data;
};

export const deleteChatSession = async (sessionId: string): Promise<void> => {
  await api.delete(`/ai-assistance/sessions/${sessionId}`);
};

export interface AIStreamMessage {
  content?: string;
  sessionId?: string;
  toolCall?: {
    name: string;
    status: 'starting' | 'executing' | 'finished';
    args?: string;
    result?: string;
  };
  citation?: string;
  error?: string;
}

export const streamAIAssistance = async (
  request: AIAssistanceRequest,
  onMessage: (msg: AIStreamMessage) => void,
  onComplete: () => void,
  onError: (error: any) => void
) => {
  const token = localStorage.getItem('accessToken');
  const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

  try {
    const response = await fetch(`${API_BASE_URL}/ai-assistance/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(request)
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) throw new Error('No reader available');

    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      
      // Keep the last partial line in the buffer
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.trim().startsWith('data: ')) {
          const content = line.trim().replace('data: ', '');
          try {
            const data = JSON.parse(content);
            if (data.error) {
              onMessage({ error: data.error });
              return;
            }
            
            const rawContent = data.content;
            if (rawContent) {
              if (rawContent.startsWith('[SESSION_ID]')) {
                onMessage({ sessionId: rawContent.replace('[SESSION_ID] ', '').trim() });
              } else if (rawContent.startsWith('[TOOL_CALL_START]')) {
                const name = rawContent.replace('[TOOL_CALL_START] ', '').trim();
                onMessage({ toolCall: { name, status: 'starting' } });
              } else if (rawContent.startsWith('[TOOL_CALL_EXEC]')) {
                const name = rawContent.replace('[TOOL_CALL_EXEC] ', '').trim();
                onMessage({ toolCall: { name, status: 'executing' } });
              } else if (rawContent.startsWith('[TOOL_CALL_RESULT]')) {
                const payload = rawContent.replace('[TOOL_CALL_RESULT] ', '').trim();
                try {
                  // New format: JSON string
                  const payloadData = JSON.parse(payload);
                  onMessage({ 
                    toolCall: { 
                      name: payloadData.name, 
                      status: 'finished', 
                      args: typeof payloadData.args === 'string' ? payloadData.args : JSON.stringify(payloadData.args), 
                      result: payloadData.result 
                    } 
                  });
                } catch (e) {
                  // Fallback to old pipe-separated format
                  const parts = payload.split('|');
                  const name = parts[0];
                  const args = parts[1];
                  const result = parts.slice(2).join('|');
                  onMessage({ toolCall: { name, status: 'finished', args, result } });
                }
              } else if (rawContent === '[TOOL_CALL_FINISHED]') {
                onMessage({ toolCall: { name: '', status: 'finished' } });
              } else if (rawContent.startsWith('[CITATION]')) {
                onMessage({ citation: rawContent.replace('[CITATION] ', '') });
              } else {
                onMessage({ content: rawContent });
              }
            }
          } catch (e) {
            console.warn("Failed to parse SSE chunk", e);
          }
        }
      }
    }
    onComplete();
  } catch (error) {
    onError(error);
  }
};
