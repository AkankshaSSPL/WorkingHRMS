import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { LoadingSkeleton } from "@/components/ui-system";
import { useAuthStore } from "@/stores/authStore";

type ProtectedRouteProps = {
  permission?: string | string[];
};

export function ProtectedRoute({ permission }: ProtectedRouteProps) {
  const location = useLocation();
  const status = useAuthStore((state) => state.status);
  const user = useAuthStore((state) => state.user);
  const initialize = useAuthStore((state) => state.initialize);
  const hasPermission = useAuthStore((state) => state.hasPermission);

  useEffect(() => {
    void initialize();
  }, [initialize]);

  if (status === "idle" || status === "loading") {
    return (
      <div className="min-h-screen bg-background p-8">
        <LoadingSkeleton rows={6} />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  // Supports either a single permission or a list of permissions that must ALL be
  // held — needed for pages like the manager Leave workspace that combine data
  // gated behind more than one permission (leave:view + approvals:view + employees:view).
  const requiredPermissions = permission ? (Array.isArray(permission) ? permission : [permission]) : [];
  const isAuthorized = requiredPermissions.every((required) => hasPermission(required));

  if (requiredPermissions.length && !isAuthorized) {
    return <Navigate to="/unauthorized" replace />;
  }

  return <Outlet />;
}