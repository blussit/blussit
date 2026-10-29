import { apiClient, UPLOAD_TIMEOUT_MS, type ApiSuccess } from "../lib/api-client";

/** Festival / broadcast campaigns (admin only). Server contract: backend/app/routes/v1/whatsapp_campaign_routes.py */

export interface CampaignCounts {
  queued: number;
  sent: number;
  delivered: number;
  read: number;
  failed: number;
  skipped: number;
}

export type CampaignStatus = "draft" | "queued" | "sending" | "done" | "cancelled" | "stopped";

export interface Campaign {
  id: string;
  name: string;
  template_name: string;
  image_url: string;
  message: string;
  rendered_message: string;
  status: CampaignStatus;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  stop_reason?: string | null;
  waiting_reason?: string | null;
  total_recipients: number;
  cost_estimate_paise: number;
  counts: CampaignCounts;
  failure_reasons?: Record<string, number>;
  materialized_count?: number;
}

export interface AudienceFilter {
  search?: string;
  center_id?: string | null;
  city?: string | null;
  last_booking_from?: string | null;
  last_booking_to?: string | null;
  no_booking?: boolean | null;
  has_active_plan?: boolean | null;
  bookings_min?: number | null;
  bookings_max?: number | null;
}

/** mode "all" = everyone matching `filter` except `excluded_ids`; "none" = only `included_ids`. */
export interface Selection {
  mode: "all" | "none" | "ids";
  filter: AudienceFilter;
  excluded_ids: string[];
  included_ids: string[];
}

export interface AudienceRow {
  id: string;
  name: string;
  phone: string;
  city: string | null;
  center: string | null;
  last_booking_at: string | null;
  bookings_count: number;
  has_active_plan: boolean;
  selected: boolean;
}

export interface AudiencePage {
  items: AudienceRow[];
  total: number;
  page: number;
  page_size: number;
  selected_count: number;
}

export interface CampaignQuote {
  recipient_count: number;
  cost_paise: number;
  cost_per_message_paise: number;
  eta_seconds: number;
  rate_per_second: number;
  max_recipients: number;
  daily_cap: number;
  sent_last_24h: number;
  template: { name: string; ready: boolean; status: string; note: string };
  problems: string[];
  can_send: boolean;
  image_url: string;
  message: string;
  rendered_message: string;
  consent_note: string;
}

export interface CampaignRecipient {
  id: string;
  phone: string;
  status: string;
  delivery_status: string | null;
  reason: string | null;
  error: string | null;
}

const BASE = "/whatsapp/campaigns";

export const campaignApi = {
  list: (page = 1) => apiClient.get<ApiSuccess<{ items: Campaign[]; total: number }>>(BASE, { params: { page } }).then((r) => r.data.data),
  get: (id: string) => apiClient.get<ApiSuccess<Campaign>>(`${BASE}/${id}`).then((r) => r.data.data),
  uploadImage: async (file: File): Promise<string> => {
    const form = new FormData();
    form.append("file", file);
    const { data } = await apiClient.post<ApiSuccess<{ url: string }>>(`${BASE}/image`, form, { headers: { "Content-Type": undefined }, timeout: UPLOAD_TIMEOUT_MS });
    return data.data.url;
  },
  create: (payload: { name: string; message: string; image_url: string }) => apiClient.post<ApiSuccess<Campaign>>(BASE, payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<{ name: string; message: string; image_url: string }>) =>
    apiClient.put<ApiSuccess<Campaign>>(`${BASE}/${id}`, payload).then((r) => r.data.data),
  audience: (selection: Selection, page: number, pageSize = 25) =>
    apiClient.post<ApiSuccess<AudiencePage>>(`${BASE}/audience/selection`, { selection }, { params: { page, page_size: pageSize } }).then((r) => r.data.data),
  preview: (id: string, selection: Selection) => apiClient.post<ApiSuccess<CampaignQuote>>(`${BASE}/${id}/preview`, { selection }).then((r) => r.data.data),
  test: (id: string) => apiClient.post<ApiSuccess<{ sent_to: string }>>(`${BASE}/${id}/test`).then((r) => r.data.data),
  send: (id: string, selection: Selection, confirmCount: number) =>
    apiClient.post<ApiSuccess<Campaign>>(`${BASE}/${id}/send`, { selection, confirm_count: confirmCount }).then((r) => r.data.data),
  cancel: (id: string) => apiClient.post<ApiSuccess<Campaign>>(`${BASE}/${id}/cancel`).then((r) => r.data.data),
  recipients: (id: string, status: string, page = 1) =>
    apiClient
      .get<ApiSuccess<{ items: CampaignRecipient[]; total: number }>>(`${BASE}/${id}/recipients`, { params: { status, page, page_size: 25 } })
      .then((r) => r.data.data),
};
