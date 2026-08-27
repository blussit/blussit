import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { User } from "../types";

export interface AuthResult {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export const authApi = {
  register: (payload: { full_name: string; email?: string; phone?: string; password: string }) =>
    apiClient.post<ApiSuccess<AuthResult>>("/auth/register", payload).then((r) => r.data.data),

  login: (payload: { identifier: string; password: string }) =>
    apiClient.post<ApiSuccess<AuthResult>>("/auth/login", payload).then((r) => r.data.data),

  me: () => apiClient.get<ApiSuccess<User>>("/auth/me").then((r) => r.data.data),

  changePassword: (payload: { current_password: string; new_password: string }) =>
    apiClient.post<ApiSuccess<null>>("/auth/change-password", payload).then((r) => r.data),

  // The OTP itself is never in this response — it's sent over WhatsApp
  // only (see backend AuthController.forgot_password).
  forgotPassword: (identifier: string) =>
    apiClient.post<ApiSuccess<{ otp_sent: boolean }>>("/auth/forgot-password", { identifier }).then((r) => r.data.data),

  resetPassword: (payload: { identifier: string; otp: string; new_password: string }) =>
    apiClient.post<ApiSuccess<null>>("/auth/reset-password", payload).then((r) => r.data),

  // Phone verification gate (a logged-in customer's first self-service
  // booking/subscription) — always the caller's own phone.
  requestPhoneVerification: () =>
    apiClient.post<ApiSuccess<{ otp_sent: boolean }>>("/auth/verify-phone/request").then((r) => r.data.data),

  confirmPhoneVerification: (otp: string) =>
    apiClient.post<ApiSuccess<User>>("/auth/verify-phone/confirm", { otp }).then((r) => r.data.data),

  // Manager/admin resets a customer's forgotten password — the generated
  // temp password is sent straight to the customer's WhatsApp and is
  // never included in this response (see backend docstring).
  resetCustomerPassword: (customerId: string) =>
    apiClient.post<ApiSuccess<null>>(`/auth/customers/${customerId}/reset-password`).then((r) => r.data),
};
