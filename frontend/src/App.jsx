import { useState } from "react";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import LoginPage from "./pages/LoginPage";
import RequestPage from "./pages/RequestPage";
import ApprovalPage from "./pages/ApprovalPage";
import DashboardPage from "./pages/DashboardPage";
import AdminPage from "./pages/AdminPage";
import "./App.css";

function AppRouter() {
  const { auth } = useAuth();
  const path = window.location.pathname;
  const [showRequest, setShowRequest] = useState(false);
  const [requestEmail, setRequestEmail] = useState("");

  // Approval page — accessible without auth (manager clicks link from email)
  if (path === "/approve") {
    return <ApprovalPage />;
  }

  // Authenticated
  if (auth) {
    if (auth.role === "admin") return <AdminPage />;
    return <DashboardPage />;
  }

  // Unauthenticated
  if (showRequest) {
    return (
      <RequestPage
        initialEmail={requestEmail}
        onBack={() => { setShowRequest(false); setRequestEmail(""); }}
      />
    );
  }

  return (
    <LoginPage
      onRequestAccess={(email) => {
        setRequestEmail(email);
        setShowRequest(true);
      }}
    />
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRouter />
    </AuthProvider>
  );
}
