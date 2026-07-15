import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, CheckCircle2, RefreshCw, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AppLayout,
  DrawerPanel,
  EmptyState,
  LoadingSkeleton,
  PageContainer,
  PageHeader,
  SectionCard,
  StatusBadge,
} from "@/components/ui-system";
import { applyLeave, getLeavePolicies, getMyLeaveWorkspace, type LeaveRequest } from "@/services/leave";
import { useAuthStore } from "@/stores/authStore";

function requestDate(request: LeaveRequest) {
  const from = request.from_date ?? request.start_date ?? "Date not set";
  const to = request.to_date ?? request.end_date ?? from;
  return from === to ? from : `${from} to ${to}`;
}

function requestTone(status: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "APPROVED") return "success";
  if (status === "PENDING") return "warning";
  if (status === "REJECTED" || status === "CANCELLED") return "danger";
  return "neutral";
}

export function MyLeavePage() {
  const queryClient = useQueryClient();
  const employeeId = useAuthStore((state) => state.user?.employee_id ?? null);
  const [applyingLeave, setApplyingLeave] = useState(false);
  const [leaveForm, setLeaveForm] = useState({ leave_type: "Casual Leave", start_date: "", end_date: "", reason: "" });

  const workspaceQuery = useQuery({
    queryKey: ["my-leave-workspace", employeeId],
    queryFn: () => getMyLeaveWorkspace(employeeId as string),
    enabled: Boolean(employeeId),
    refetchInterval: 15000,
  });
  const policiesQuery = useQuery({ queryKey: ["leave-policies"], queryFn: getLeavePolicies, enabled: applyingLeave });
  const applyMutation = useMutation({
    mutationFn: applyLeave,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["my-leave-workspace", employeeId] });
      setApplyingLeave(false);
      setLeaveForm({ leave_type: "Casual Leave", start_date: "", end_date: "", reason: "" });
    },
  });

  const workspace = workspaceQuery.data;
  const paidRemaining = (workspace?.balances ?? [])
    .filter((balance) => ["Paid Leave", "Casual Leave"].includes(balance.leave_type))
    .reduce((total, balance) => total + balance.remaining, 0);
  const pendingCount = (workspace?.history ?? []).filter((request) => request.status === "PENDING").length;

  if (!employeeId) {
    return (
      <AppLayout>
        <PageContainer>
          <PageHeader title="My Leave" description="Your leave balances, history, and requests." />
          <EmptyState
            title="No linked employee record"
            description="Your account isn't linked to an employee record yet, so leave data can't be shown. Contact HR to have your account linked."
          />
        </PageContainer>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <PageContainer>
        <PageHeader
          title="My Leave"
          description="Your leave balances, history, and requests."
          actions={
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={() => workspaceQuery.refetch()} disabled={workspaceQuery.isFetching}>
                <RefreshCw className={workspaceQuery.isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
                Refresh
              </Button>
              <Button onClick={() => setApplyingLeave(true)}>
                <Send className="h-4 w-4" />
                Apply Leave
              </Button>
            </div>
          }
        />

        {workspaceQuery.isLoading ? <LoadingSkeleton rows={7} /> : null}
        {workspaceQuery.isError ? (
          <EmptyState
            title="Unable to load your leave data"
            description="Your leave workspace could not be retrieved. Check that the backend is running, then refresh."
            actionLabel="Try again"
            onAction={() => workspaceQuery.refetch()}
          />
        ) : null}

        {workspace ? (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              <SectionCard className="border border-emerald-200 bg-emerald-50 text-emerald-800">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-medium uppercase opacity-75">Paid Days Remaining</p>
                    <p className="mt-2 text-2xl font-semibold">{paidRemaining}</p>
                  </div>
                  <CheckCircle2 className="h-5 w-5 opacity-70" />
                </div>
              </SectionCard>
              <SectionCard className="border border-amber-200 bg-amber-50 text-amber-800">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-medium uppercase opacity-75">Your Pending Requests</p>
                    <p className="mt-2 text-2xl font-semibold">{pendingCount}</p>
                  </div>
                  <CalendarDays className="h-5 w-5 opacity-70" />
                </div>
              </SectionCard>
            </div>

            <SectionCard title="Leave Balances" description="Your current entitlement by leave type.">
              <div className="overflow-hidden rounded-lg border">
                <div className="grid grid-cols-[minmax(160px,1fr)_80px_80px_80px] gap-3 bg-muted px-4 py-3 text-xs font-medium uppercase text-muted-foreground">
                  <span>Leave Type</span>
                  <span className="text-right">Allocated</span>
                  <span className="text-right">Used</span>
                  <span className="text-right">Remaining</span>
                </div>
                {workspace.balances.map((balance) => (
                  <div
                    key={`${balance.leave_type}-${balance.year}`}
                    className="grid grid-cols-[minmax(160px,1fr)_80px_80px_80px] gap-3 border-t px-4 py-3 text-sm"
                  >
                    <span>{balance.leave_type}</span>
                    <span className="text-right">{balance.allocated}</span>
                    <span className="text-right">{balance.used}</span>
                    <span className="text-right font-semibold text-emerald-700">{balance.remaining}</span>
                  </div>
                ))}
                {!workspace.balances.length ? <p className="border-t p-6 text-center text-sm text-muted-foreground">No leave balances found.</p> : null}
              </div>
            </SectionCard>

            <SectionCard title="My Requests" description="Your leave request history, including pending, approved, and past requests.">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {workspace.history.map((request) => (
                  <div key={request.id} className="rounded-md border p-4">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-sm font-semibold">{request.leave_type}</p>
                      <StatusBadge status={request.status} tone={requestTone(request.status)} />
                    </div>
                    <p className="mt-3 text-sm">{requestDate(request)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{request.total_days} working day{request.total_days === 1 ? "" : "s"}</p>
                  </div>
                ))}
                {!workspace.history.length ? (
                  <div className="md:col-span-2 xl:col-span-3">
                    <EmptyState title="No leave requests yet" description="Your submitted leave requests will appear here." />
                  </div>
                ) : null}
              </div>
            </SectionCard>
          </>
        ) : null}

        <DrawerPanel open={applyingLeave} title="Apply Leave" onClose={() => setApplyingLeave(false)}>
          <div className="space-y-4">
            <div>
              <h3 className="text-base font-semibold">Leave request</h3>
              <p className="mt-1 text-sm text-muted-foreground">Submit a leave request for yourself. Approval and attendance updates continue through the existing workflow.</p>
            </div>
            <FormField label="Leave type">
              <select
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                value={leaveForm.leave_type}
                onChange={(event) => setLeaveForm((current) => ({ ...current, leave_type: event.target.value }))}
              >
                {(policiesQuery.data ?? []).map((policy) => (
                  <option key={policy.id} value={policy.name}>
                    {policy.name}
                    {policy.affects_payroll ? " · Payroll impact" : ""}
                  </option>
                ))}
              </select>
            </FormField>
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField label="From date">
                <Input
                  type="date"
                  value={leaveForm.start_date}
                  onChange={(event) =>
                    setLeaveForm((current) => ({ ...current, start_date: event.target.value, end_date: current.end_date || event.target.value }))
                  }
                />
              </FormField>
              <FormField label="To date">
                <Input type="date" value={leaveForm.end_date} onChange={(event) => setLeaveForm((current) => ({ ...current, end_date: event.target.value }))} />
              </FormField>
            </div>
            <FormField label="Reason">
              <textarea
                className="min-h-24 w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
                value={leaveForm.reason}
                onChange={(event) => setLeaveForm((current) => ({ ...current, reason: event.target.value }))}
              />
            </FormField>
            {applyMutation.isError ? (
              <p className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">Leave request could not be submitted. Check balances, dates, and overlapping requests.</p>
            ) : null}
            <div className="flex justify-end gap-2 border-t pt-4">
              <Button variant="outline" onClick={() => setApplyingLeave(false)}>Cancel</Button>
              <Button
                disabled={applyMutation.isPending || !leaveForm.start_date || !leaveForm.end_date}
                onClick={() =>
                  employeeId &&
                  applyMutation.mutate({
                    employee_id: employeeId,
                    leave_type: leaveForm.leave_type,
                    start_date: leaveForm.start_date,
                    end_date: leaveForm.end_date,
                    reason: leaveForm.reason || undefined,
                  })
                }
              >
                {applyMutation.isPending ? "Submitting..." : "Submit Request"}
              </Button>
            </div>
          </div>
        </DrawerPanel>
      </PageContainer>
    </AppLayout>
  );
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="space-y-1.5 text-sm">
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}