export interface ToolCallInfo {
  name: string;
  args?: string;
  result?: string;
  status: 'starting' | 'executing' | 'finished';
}

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: ToolCallInfo[];
  citations?: string[];
  isExecuting?: boolean;
}
