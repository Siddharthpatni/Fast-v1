import { StrictMode } from 'react';
// Fonts are served from this site, so no visitor's IP address goes to Google Fonts
import '@fontsource/fira-sans/latin-400.css';
import '@fontsource/fira-sans/latin-500.css';
import '@fontsource/fira-sans/latin-600.css';
import '@fontsource/fira-sans/latin-700.css';
import '@fontsource/fira-sans/latin-800.css';
import '@fontsource/fira-sans-condensed/latin-500.css';
import '@fontsource/fira-sans-condensed/latin-700.css';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { LangProvider } from './LangProvider';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LangProvider>
      <App />
    </LangProvider>
  </StrictMode>,
);
