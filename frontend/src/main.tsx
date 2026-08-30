import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './i18n'; // Initialize i18n
import App from './App';
import '@neuronection/assistant-ui/styles.css';
import './index.css';
import './theme.css';
import { registerSW } from 'virtual:pwa-register';

// Register service worker
const updateSW = registerSW({
  onNeedRefresh() {
    if (confirm('New content available. Reload?')) {
      updateSW(true);
    }
  },
  onOfflineReady() {
    console.log('App ready to work offline');
  },
});

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    {/* v7: relative-splat-path is the default; the future flag is gone. */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);