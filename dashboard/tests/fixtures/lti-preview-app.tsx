import React from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter, Link, Navigate, Route, Routes } from 'react-router-dom';
import { LTICourseView } from '../../src/pages/LTICourseView';
import { LTIFilePicker } from '../../src/pages/LTIFilePicker';
import { ToastProvider } from '../../src/context/ToastContext';
import '../../src/index.css';

export function Preview() {
  return (
    <HashRouter>
      <ToastProvider>
        <aside className="p-4 border-b border-[var(--border-primary)] bg-[var(--surface-secondary)]">
          <p className="font-semibold">Synthetic local LTI visual fixture — no LMS connection</p>
          <p className="text-sm mb-3">Actual course and file picker components with synthetic session and in-memory scan data. Remote actions are disabled.</p>
          <nav className="flex flex-wrap gap-4" aria-label="Fixture views">
            <Link className="btn-secondary" to="/course/fixture-course">Course view</Link>
            <Link className="btn-secondary" to="/picker/fixture-course">File picker</Link>
          </nav>
        </aside>
        <Routes>
          <Route path="/course/:courseId" element={<LTICourseView />} />
          <Route path="/picker/:courseId" element={<LTIFilePicker />} />
          <Route path="*" element={<Navigate to="/course/fixture-course" replace />} />
        </Routes>
      </ToastProvider>
    </HashRouter>
  );
}

createRoot(document.getElementById('root')!).render(<Preview />);
