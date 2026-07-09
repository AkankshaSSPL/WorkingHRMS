import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, FileCheck2, FileClock, FileText, Plus, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AppLayout, DrawerPanel, EmptyState, LoadingSkeleton, PageContainer, PageHeader, SectionCard, StatusBadge } from "@/components/ui-system";
import { createDocument, getDocuments } from "@/services/documents";
import { getEmployees } from "@/services/employees";
import { getLookups } from "@/services/lookups";

export function DocumentsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ employee_id: "", document_type: "", document_url: "", status: "" });
  const documentsQuery = useQuery({ queryKey: ["documents"], queryFn: getDocuments });
  const employeesQuery = useQuery({ queryKey: ["employees"], queryFn: getEmployees, enabled: adding });
  const lookupsQuery = useQuery({
    queryKey: ["lookups", "document-form"],
    queryFn: () => getLookups(["document_type", "document_status"]),
    enabled: adding,
  });
  const createMutation = useMutation({
    mutationFn: createDocument,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      setAdding(false);
      setForm({ employee_id: "", document_type: "", document_url: "", status: "" });
    },
  });
  const documents = documentsQuery.data ?? [];
  const filtered = useMemo(() => {
    const value = search.trim().toLowerCase();
    if (!value) return documents;
    return documents.filter((document) => [document.employee_name, document.document_type, document.status].some((item) => item.toLowerCase().includes(value)));
  }, [documents, search]);
  const verified = documents.filter((document) => document.status === "VERIFIED").length;
  const pending = documents.filter((document) => document.status === "PENDING").length;

  return (
    <AppLayout>
      <PageContainer>
        <PageHeader
          title="Document Control"
          description="Employee statutory, identity, banking, and employment document records."
          actions={<Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" />Add Document</Button>}
        />
        <div className="grid gap-3 md:grid-cols-3">
          <SectionCard className="border-blue-200 bg-blue-50"><Metric icon={FileText} label="Total Documents" value={documents.length} /></SectionCard>
          <SectionCard className="border-emerald-200 bg-emerald-50"><Metric icon={FileCheck2} label="Verified" value={verified} /></SectionCard>
          <SectionCard className="border-amber-200 bg-amber-50"><Metric icon={FileClock} label="Pending Review" value={pending} /></SectionCard>
        </div>
        <SectionCard
          title="Employee Documents"
          description="Central document register linked to employee master records."
          action={<div className="relative w-64"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" placeholder="Search documents" value={search} onChange={(event) => setSearch(event.target.value)} /></div>}
        >
          {documentsQuery.isLoading ? <LoadingSkeleton rows={5} /> : null}
          {!documentsQuery.isLoading && !filtered.length ? <EmptyState title="No employee documents" description="Add a document record to begin the employee document register." /> : null}
          <div className="divide-y rounded-md border">
            {filtered.map((document) => (
              <div key={document.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="rounded-md bg-blue-50 p-2 text-blue-700"><FileText className="h-4 w-4" /></div>
                  <div className="min-w-0"><p className="truncate text-sm font-semibold">{document.document_type}</p><p className="truncate text-xs text-muted-foreground">{document.employee_name}</p></div>
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={document.status} tone={document.status === "VERIFIED" ? "success" : document.status === "REJECTED" ? "danger" : "warning"} />
                  <Button variant="ghost" size="icon" aria-label="Open document" onClick={() => window.open(document.document_url, "_blank", "noopener,noreferrer")}><ExternalLink className="h-4 w-4" /></Button>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
        <DrawerPanel open={adding} title="Add Document" onClose={() => setAdding(false)}>
          <div className="space-y-4">
            <Field label="Employee"><select className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={form.employee_id} onChange={(event) => setForm((current) => ({ ...current, employee_id: event.target.value }))}><option value="">Select employee</option>{(employeesQuery.data?.items ?? []).map((employee) => <option key={employee.id} value={employee.id}>{employee.name}</option>)}</select></Field>
            <Field label="Document type"><select className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={form.document_type} onChange={(event) => setForm((current) => ({ ...current, document_type: event.target.value }))}><option value="">Select document type</option>{(lookupsQuery.data?.document_type ?? []).map((item) => <option key={item.id} value={item.label}>{item.label}</option>)}</select></Field>
            <Field label="Document URL"><Input placeholder="https:// or internal storage path" value={form.document_url} onChange={(event) => setForm((current) => ({ ...current, document_url: event.target.value }))} /></Field>
            <Field label="Status"><select className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value }))}><option value="">Select status</option>{(lookupsQuery.data?.document_status ?? []).map((item) => <option key={item.id} value={item.code}>{item.label}</option>)}</select></Field>
            {createMutation.isError ? <p className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">Document record could not be created.</p> : null}
            <div className="flex justify-end gap-2 border-t pt-4"><Button variant="outline" onClick={() => setAdding(false)}>Cancel</Button><Button disabled={createMutation.isPending || !form.employee_id || !form.document_type || !form.status || !form.document_url} onClick={() => createMutation.mutate(form)}>{createMutation.isPending ? "Adding..." : "Add Document"}</Button></div>
          </div>
        </DrawerPanel>
      </PageContainer>
    </AppLayout>
  );
}

function Metric({ icon: Icon, label, value }: { icon: typeof FileText; label: string; value: number }) {
  return <div className="flex items-center justify-between"><div><p className="text-xs font-medium uppercase opacity-70">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div><Icon className="h-5 w-5 opacity-70" /></div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="space-y-1.5 text-sm"><span className="font-medium">{label}</span>{children}</label>;
}
