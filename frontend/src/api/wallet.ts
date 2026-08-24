import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { CaptainWallet, WalletTransaction, WithdrawalRequest } from "../types";

export interface BankDetailsPayload {
  bank_account_number: string;
  bank_ifsc: string;
  bank_account_holder: string;
}

export const walletApi = {
  myWallet: () => apiClient.get<ApiSuccess<CaptainWallet>>("/wallet/my").then((r) => r.data.data),

  captainWallet: (captainId: string) =>
    apiClient.get<ApiSuccess<CaptainWallet>>(`/wallet/captain/${captainId}`).then((r) => r.data.data),

  topUp: (amount: number) => apiClient.post<ApiSuccess<CaptainWallet>>("/wallet/top-up", { amount }).then((r) => r.data.data),

  updateBankDetails: (payload: BankDetailsPayload) =>
    apiClient.put<ApiSuccess<CaptainWallet>>("/wallet/bank-details", payload).then((r) => r.data.data),

  myTransactions: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<WalletTransaction>>("/wallet/my/transactions", { params }).then((r) => r.data),

  requestWithdrawal: (amount: number) =>
    apiClient.post<ApiSuccess<WithdrawalRequest>>("/wallet/withdrawals", { amount }).then((r) => r.data.data),

  myWithdrawals: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<WithdrawalRequest>>("/wallet/withdrawals/my", { params }).then((r) => r.data),

  pendingWithdrawals: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<WithdrawalRequest>>("/wallet/withdrawals/pending", { params }).then((r) => r.data),

  reviewWithdrawal: (withdrawalId: string, status: "approved" | "rejected" | "paid", review_note?: string) =>
    apiClient
      .put<ApiSuccess<WithdrawalRequest>>(`/wallet/withdrawals/${withdrawalId}/review`, { status, review_note })
      .then((r) => r.data.data),

  adminAdjust: (captainId: string, amount: number, description: string) =>
    apiClient.post<ApiSuccess<CaptainWallet>>(`/wallet/captain/${captainId}/adjust`, { amount, description }).then((r) => r.data.data),
};
