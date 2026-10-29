import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { RulesRecord } from "../components/admin/CouponRulesFields";

export type BundleStatus = "unused" | "partly_used" | "used" | "expired" | "disabled";

export interface BundleCounts {
  total: number;
  redemptions: number;
  unused: number;
  partly_used: number;
  used: number;
  expired: number;
  disabled: number;
}

export interface CouponBundle extends RulesRecord {
  id: string;
  name: string;
  description?: string | null;
  offer_kind: "standard" | "free_addon_with_service";
  coupon_type: "flat" | "percentage";
  value: number;
  min_order_value: number;
  max_discount_amount?: number | null;
  eligible_service_keywords: string[];
  free_addon_keywords: string[];
  uses_per_coupon: number;
  usage_limit_per_user: number;
  valid_until: string | null;
  is_active: boolean;
  state: "Active" | "Expired" | "Disabled";
  created_at: string;
  counts: BundleCounts;
}

export interface BundleCodeRow {
  id: string;
  code: string;
  status: BundleStatus;
  status_label: string;
  is_active: boolean;
  used_count: number;
  uses_total: number;
  remaining: number;
  added_at: string | null;
  used_at: string | null;
  used_by: { id: string; name: string; phone?: string | null } | null;
  booking_id: string | null;
  booking_number: string | null;
}

export interface CodeUse {
  used_at: string | null;
  booking_id: string;
  booking_number: string | null;
  user: { id: string; name: string; phone?: string | null };
}

/** [row number, code, problem or null] */
export type PreviewRow = [number, string, string | null];

export interface BundlePreview {
  rows: PreviewRow[];
  total: number;
  ok: number;
  problems: number;
  format: string;
}

export interface ImportSummary {
  added: number;
  skipped_duplicates: number;
  invalid: number;
  bundle: CouponBundle;
}

/** The benefit + rules one bundle shares (the create / edit payload). */
export interface BundleSettings extends RulesRecord {
  name: string;
  description?: string | null;
  offer_kind: "standard" | "free_addon_with_service";
  coupon_type: "flat" | "percentage";
  value: number;
  min_order_value: number;
  max_discount_amount?: number | null;
  eligible_service_keywords: string[];
  free_addon_keywords: string[];
  uses_per_coupon: number;
  usage_limit_per_user: number;
  valid_until: string | null;
}

export interface CodeFilters {
  status?: BundleStatus | "";
  search?: string;
  sort_by?: "added" | "used" | "code" | "uses";
  sort_order?: 1 | -1;
  added_from?: string;
  added_to?: string;
  used_from?: string;
  used_to?: string;
}

const clean = <T extends object>(f: T) => Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "" && v !== undefined && v !== null));
const BASE = "/coupons/bundles";

export const couponBundleApi = {
  list: (params: { page?: number; page_size?: number; search?: string }) =>
    apiClient.get<ApiPaginated<CouponBundle>>(BASE, { params: clean(params) }).then((r) => r.data),
  get: (id: string) => apiClient.get<ApiSuccess<CouponBundle>>(`${BASE}/${id}`).then((r) => r.data.data),
  sample: () => apiClient.get<Blob>(`${BASE}/sample.csv`, { responseType: "blob" }).then((r) => r.data),
  preview: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient.post<ApiSuccess<BundlePreview>>(`${BASE}/preview`, form, { headers: { "Content-Type": "multipart/form-data" } }).then((r) => r.data.data);
  },
  create: (payload: BundleSettings & { codes: string[] }) => apiClient.post<ApiSuccess<ImportSummary>>(BASE, payload, { timeout: 180_000 }).then((r) => r.data.data),
  addCodes: (id: string, codes: string[]) => apiClient.post<ApiSuccess<ImportSummary>>(`${BASE}/${id}/codes`, { codes }, { timeout: 180_000 }).then((r) => r.data.data),
  update: (id: string, payload: Partial<BundleSettings> & { is_active?: boolean }) =>
    apiClient.put<ApiSuccess<CouponBundle>>(`${BASE}/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete<ApiSuccess<{ mode: "deleted" | "disabled"; codes_removed: number }>>(`${BASE}/${id}`).then((r) => r.data),
  codes: (id: string, params: CodeFilters & { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<BundleCodeRow>>(`${BASE}/${id}/codes`, { params: clean(params) }).then((r) => r.data),
  setCodeActive: (id: string, codeId: string, is_active: boolean) => apiClient.put(`${BASE}/${id}/codes/${codeId}`, { is_active }).then((r) => r.data),
  uses: (id: string, codeId: string) => apiClient.get<ApiSuccess<CodeUse[]>>(`${BASE}/${id}/codes/${codeId}/uses`).then((r) => r.data.data),
  exportCsv: (id: string, filters: CodeFilters) =>
    apiClient.get<Blob>(`${BASE}/${id}/export.csv`, { params: clean(filters), responseType: "blob", timeout: 180_000 }).then((r) => r.data),
};
