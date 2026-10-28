import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";

export type FinanceType = "credit" | "debit";

export interface FinanceEntry {
  id: string;
  title: string;
  type: FinanceType;
  amount_paise: number;
  amount_rupees: number;
  /** IST calendar day, YYYY-MM-DD. */
  entry_date: string;
  category: string | null;
  description: string;
  done_by: string;
  reference: string | null;
  created_at: string | null;
  updated_at: string | null;
  is_deleted: boolean;
  delete_reason: string | null;
}

export interface FinanceEntryInput {
  title: string;
  type: FinanceType;
  amount_paise: number;
  entry_date?: string;
  category?: string | null;
  description?: string;
  done_by: string;
  reference?: string | null;
}

export interface FinanceFilters {
  type?: FinanceType | "";
  category?: string;
  from?: string;
  to?: string;
  q?: string;
  done_by?: string;
  include_voided?: boolean;
}

export interface FinanceSummary {
  period: { from: string; to: string };
  bookings_earning_paise: number;
  bookings_earning_definition: string;
  credits_paise: number;
  credit_count: number;
  debits_paise: number;
  debit_count: number;
  net_other_paise: number;
  net_period_paise: number;
  total_balance_paise: number;
  plan_sales_paise: number;
  plan_sales_note: string;
  by_category: { category: string; credit_paise: number; debit_paise: number }[];
  series_unit: "day" | "month";
  series: { key: string; credit_paise: number; debit_paise: number }[];
}

const clean = (f: FinanceFilters) => Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "" && v !== undefined && v !== false));

export const financeApi = {
  list: (filters: FinanceFilters & { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<FinanceEntry>>("/admin/finance/entries", { params: clean(filters) }).then((r) => r.data),
  create: (payload: FinanceEntryInput) => apiClient.post<ApiSuccess<FinanceEntry>>("/admin/finance/entries", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<FinanceEntryInput>) =>
    apiClient.patch<ApiSuccess<FinanceEntry>>(`/admin/finance/entries/${id}`, payload).then((r) => r.data.data),
  void: (id: string, reason: string) =>
    apiClient.post<ApiSuccess<FinanceEntry>>(`/admin/finance/entries/${id}/void`, { reason }).then((r) => r.data.data),
  summary: (from: string, to: string) =>
    apiClient.get<ApiSuccess<FinanceSummary>>("/admin/finance/summary", { params: { from, to } }).then((r) => r.data.data),
  categories: () =>
    apiClient.get<ApiSuccess<{ suggested: string[]; used: string[]; all: string[] }>>("/admin/finance/categories").then((r) => r.data.data),
  exportCsv: (filters: FinanceFilters) =>
    apiClient.get<Blob>("/admin/finance/entries.csv", { params: clean(filters), responseType: "blob" }).then((r) => r.data),
};
