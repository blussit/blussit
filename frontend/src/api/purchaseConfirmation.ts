import { apiClient, type ApiSuccess } from "../lib/api-client";

export interface PurchaseConfirmation {
  id: string;
  token: string;
  type: "booking" | "subscription";
  reference_id: string;
  customer_id: string | null;
  payload: {
    booking_number?: string;
    scheduled_date?: string;
    scheduled_slot?: string;
    plan_name?: string;
  };
  expires_at: string;
}

export const purchaseConfirmationApi = {
  // Deliberately unauthenticated on the wire — see the backend route's
  // docstring. Security comes from the token being unguessable + short-
  // lived, not from a session.
  get: (token: string) => apiClient.get<ApiSuccess<PurchaseConfirmation>>(`/purchase-confirmations/${token}`).then((r) => r.data.data),
};
