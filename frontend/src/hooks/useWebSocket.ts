import { useEffect, useState } from 'react';

export interface WebSocketMessage {
  type: string;
  event: string;
  data: unknown;
}

export function useWebSocket<T = unknown>(url: string, events: string[]) {
  const [socket, setSocket] = useState<WebSocket | null>(null);
  const [messages, setMessages] = useState<T[]>([]);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    try {
      const newSocket = new WebSocket(url);

      newSocket.onopen = () => {
        setIsConnected(true);
        events.forEach(event => {
          newSocket.send(JSON.stringify({ type: 'subscribe', event }));
        });
      };

      newSocket.onclose = () => {
        setIsConnected(false);
      };

      newSocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setMessages(prev => [...prev, data as T]);
        } catch {
          setMessages(prev => [...prev, event.data as unknown as T]);
        }
      };

      setSocket(newSocket);

      return () => {
        newSocket.close();
      };
    } catch (error) {
      // Use a proper logger or error boundary in production
      console.error('WebSocket connection failed:', error);
    }
  }, [url, events]);

  const sendMessage = (event: string, data: unknown) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ event, data }));
    }
  };

  return { socket, messages, isConnected, sendMessage };
}
