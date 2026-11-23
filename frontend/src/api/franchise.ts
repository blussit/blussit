import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { CaptainPayManagedBy, ServiceCenterOperationType } from "../types";

/** Admin → Franchise (ARCHITECTURE.md §7.13). Money is integer paise; the UI
 *  converts to whole rupees only when displaying and when reading a typed amount.
 *  Shapes mirror backend/app/services/franchise_service.py. */

export type FranchiseSettlementKind = "pay_partner" | "receive_from_partner" | "franchise_fee" | "kit_sale";
export type FranchiseMethod = "cash" | "bank" | "upi" | "other";

export interface FranchisePartner {
  name?: string | null;
  phone?: string | null;
  email?: string | null;
}

/** One row of GET /franchise/centers (also the `center` head of a statement). */
export interface FranchiseCenterRow {
  id: string;
  name: string;
  code?: string | null;
  is_active?: boolean;
  status?: "active" | "paused" | "archived";
  status_label?: string;
  is_deleted?: boolean;
  /** Current type — a center that stopped being a franchise still lists, as self_operated. */
  type: ServiceCenterOperationType;
  partner?: FranchisePartner | null;
  revenue_share_percent: number | null;
  captain_pay_managed_by?: CaptainPayManagedBy | null;
  effective_from?: string | null;
  kits_purchased: number;
  kits_assigned: number;
  captains_count: number;
  kits_warning?: string | null;
  bookings_completed: number;
  base_paise: number;
  share_accrued_paise: number;
  cash_collected_paise: number;
  online_collected_paise: number;
  paid_back_cash_paise: number;
  cash_held_paise: number;
  settled_paid_paise: number;
  settled_received_paise: number;
  /** Positive = Blussit owes the partner; negative = the partner owes Blussit. */
  net_payable_paise: number;
  payable_paise: number;
  receivable_paise: number;
  direction: "owed_to_partner" | "owed_by_partner" | "settled";
  franchise_fee_paise: number;
  fee_received_paise: number;
  fee_pending_paise: number;
  fee_status: "none" | "pending" | "partial" | "received";
  kit_sales_paise: number;
}

export interface FranchiseCentersResponse {
  items: FranchiseCenterRow[];
  totals: { payable_paise: number; receivable_paise: number; share_accrued_paise: number; fee_pending_paise: number };
}

export interface FranchiseLedgerRow {
  id: string;
  /** share | share_adjust | settlement_pay | settlement_receive | franchise_fee | kit_sale */
  kind: string;
  /** pay_partner | receive_from_partner | franchise_fee | kit_sale — null for share rows. */
  settlement_kind: FranchiseSettlementKind | null;
  amount_paise: number;
  booking_id?: string | null;
  booking_number?: string | null;
  method?: FranchiseMethod | null;
  paid_on?: string | null;
  note?: string;
  voided: boolean;
  void_reason?: string | null;
  finance_entry_id?: string | null;
  actor?: { id?: string; role?: string; name?: string } | null;
  created_at?: string | null;
}

interface StatementWindow {
  bookings_completed: number;
  base_paise: number;
  share_accrued_paise: number;
  cash_collected_paise: number;
  online_collected_paise: number;
  paid_back_cash_paise: number;
  cash_held_paise: number;
}

export interface FranchiseStatement {
  center: Omit<FranchiseCenterRow, "bookings_completed" | "base_paise" | "share_accrued_paise" | "cash_collected_paise" | "online_collected_paise" | "paid_back_cash_paise" | "cash_held_paise" | "settled_paid_paise" | "settled_received_paise" | "net_payable_paise" | "payable_paise" | "receivable_paise" | "direction" | "franchise_fee_paise" | "fee_received_paise" | "fee_pending_paise" | "fee_status" | "kit_sales_paise">;
  month: string | null;
  all_time: StatementWindow & {
    settled_paid_paise: number;
    settled_received_paise: number;
    net_payable_paise: number;
    payable_paise: number;
    receivable_paise: number;
    direction: FranchiseCenterRow["direction"];
  };
  /** Only when a month was asked for. */
  month_view?: StatementWindow & { net_paise: number; from: string; to: string };
  fee: { franchise_fee_paise: number; fee_received_paise: number; fee_pending_paise: number; fee_status: FranchiseCenterRow["fee_status"] };
  kit_sales_paise: number;
  settlements: FranchiseLedgerRow[];
  ledger: FranchiseLedgerRow[];
}

export interface SettlementInput {
  kind: FranchiseSettlementKind;
  amount_paise: number;
  method: FranchiseMethod;
  /** IST day, at most 1 day ahead. */
  paid_on: string;
  note?: string;
  /** 8-80 characters; one per opening of the dialog. */
  idempotency_key: string;
}

type WithMessage<T> = ApiSuccess<T>;

export const franchiseApi = {
  centers: () => apiClient.get<ApiSuccess<FranchiseCentersResponse>>("/franchise/centers").then((r) => r.data.data),
  statement: (centerId: string, month: string) =>
    apiClient.get<ApiSuccess<FranchiseStatement>>(`/franchise/centers/${centerId}/statement`, { params: month ? { month } : {} }).then((r) => r.data.data),
  settle: (centerId: string, payload: SettlementInput) =>
    apiClient.post<WithMessage<FranchiseLedgerRow & { replayed?: boolean }>>(`/franchise/centers/${centerId}/settlements`, payload).then((r) => r.data),
  voidSettlement: (centerId: string, settlementId: string, reason: string) =>
    apiClient.post<WithMessage<FranchiseLedgerRow>>(`/franchise/centers/${centerId}/settlements/${settlementId}/void`, { reason }).then((r) => r.data),
};
