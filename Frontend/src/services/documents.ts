import { apiDelete, apiGet, apiPost } from "@/services/api";

export type EmployeeDocumentRecord = {
  id: string;
  employee_id: string;
  employee_name: string;
  document_type: string;
  document_url: string;
  status: string;
  verified_at?: string | null;
  created_at?: string | null;
};

export function getDocuments() {
  return apiGet<EmployeeDocumentRecord[]>("/documents");
}

export function createDocument(payload: { employee_id: string; document_type: string; document_url: string; status?: string }) {
  return apiPost<EmployeeDocumentRecord>("/documents", payload);
}

export function verifyDocument(documentId: string) {
  return apiPost<EmployeeDocumentRecord>(`/documents/${documentId}/verify`);
}

export function rejectDocument(documentId: string, reason?: string) {
  return apiPost<EmployeeDocumentRecord>(`/documents/${documentId}/reject`, { reason });
}

export function deleteDocument(documentId: string) {
  return apiDelete<{ status: string; document_id: string }>(`/documents/${documentId}`);
}