import React, { ReactNode } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import { ThemeProvider } from './context/ThemeContext';
import { ProtectedRoute } from './components/auth/ProtectedRoute';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Navbar } from './components/layout/Navbar';
import { Sidebar } from './components/layout/Sidebar';
import { Login } from './pages/Login';
import { Signup } from './pages/Signup';
import { VerifyMagicLink } from './pages/VerifyMagicLink';
import { Dashboard } from './pages/Dashboard';
import { Upload } from './pages/Upload';
import { History } from './pages/History';
import { ScanDetail } from './pages/ScanDetail';
import { FocusOrderDetail } from './pages/FocusOrderDetail';
import { Issues } from './pages/Issues';
import { Remediate } from './pages/Remediate';
import { BulkUpload } from './pages/BulkUpload';
import Settings from './pages/Settings';
import { Integrations } from './pages/Integrations';
import { IntegrationsSettings } from './pages/IntegrationsSettings';
import { CloudFiles } from './pages/CloudFiles';
import CanvasCourses from './pages/CanvasCourses';
import { AdminDashboard } from './pages/AdminDashboard';
import { ReviewQueuePage } from './pages/ReviewQueuePage';
import { DocumentReviewPage } from './pages/DocumentReviewPage';
import { CookieBanner } from './components/CookieBanner';
import { Analytics } from './components/Analytics';
import { LTICourseView } from './pages/LTICourseView';
import { LTIReportView } from './pages/LTIReportView';
import { LTIFilePicker } from './pages/LTIFilePicker';

const queryClient = new QueryClient();

interface AppLayoutProps {
  children: ReactNode;
}

function AppLayout({ children }: AppLayoutProps): React.ReactElement {
  return (
    <div
      className="min-h-screen"
      style={{ backgroundColor: 'var(--surface-primary)' }}
    >
      {/* Skip link for keyboard users (WCAG 2.4.1) */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-[100] focus:px-4 focus:py-2 focus:rounded-lg focus:text-white focus:font-medium"
        style={{ backgroundColor: 'var(--accent-primary)' }}
      >
        Skip to main content
      </a>
      <Navbar />
      <div className="flex">
        <Sidebar />
        <main id="main-content" className="flex-1 min-w-0 pb-20 lg:pb-0" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}

function App(): React.ReactElement {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <ToastProvider>
            <BrowserRouter>
            <ErrorBoundary scope="Application">
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/signup" element={<Signup />} />
              <Route path="/auth/verify" element={<VerifyMagicLink />} />
              {/* LTI routes — auth via LTI token, not session cookies */}
              <Route path="/lti/course/:courseId" element={<LTICourseView />} />
              <Route path="/lti/report/:scanId" element={<LTIReportView />} />
              <Route path="/lti/pick/:courseId" element={<LTIFilePicker />} />
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <Dashboard />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/upload"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <Upload />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/history"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <History />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/scan/:id"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <ScanDetail />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/focus-order/:id"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <FocusOrderDetail />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <Settings />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/issues"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <Issues />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/remediate/:scanId"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <Remediate />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/bulk-upload"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <BulkUpload />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/integrations"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <Integrations />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/integrations/settings"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <IntegrationsSettings />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/integrations/files"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <CloudFiles />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route path="/integrations/canvas" element={<ProtectedRoute><AppLayout><CanvasCourses /></AppLayout></ProtectedRoute>} />
            <Route
              path="/admin"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <AdminDashboard />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/review"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <ReviewQueuePage />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/review/:scanId"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <DocumentReviewPage />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            {/* Alias routes for easier navigation and testing */}
            <Route
              path="/alerts"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <IntegrationsSettings />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route
              path="/cloud-files"
              element={
                <ProtectedRoute>
                  <AppLayout>
                    <CloudFiles />
                  </AppLayout>
                </ProtectedRoute>
              }
            />
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
          </Routes>
          </ErrorBoundary>
          </BrowserRouter>
          <CookieBanner />
          <Analytics />
        </ToastProvider>
      </AuthProvider>
    </ThemeProvider>
  </QueryClientProvider>
  );
}

export default App;
