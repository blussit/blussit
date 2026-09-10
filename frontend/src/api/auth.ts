import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { User } from "../types";

export interface AuthResult {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export const authApi = {
  register: (payload: { full_name: string; email?: string; phone?: string; password: string; guest?: boolean }) =>
    apiClient.post<ApiSuccess<AuthResult>>("/auth/register", payload).then((r) => r.data.data),

  login: (payload: { identifier: string; password: string }) =>
    apiClient.post<ApiSuccess<AuthResult>>("/auth/login", payload).then((r) => r.data.data),

  me: () => apiClient.get<ApiSuccess<User>>("/auth/me").then((r) => r.data.data),

  logout: () => apiClient.post<{ success: boolean }>("/auth/logout").then((r) => r.data),
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

// ---- MSG91 OTP widget (server-verified) -----------------------------------
export const otpWidgetApi = {
  config: () =>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         
    apiClient
      .get<ApiSuccess<{ enabled: boolean; widget_id: string | null; token_auth: string | null }>>("/auth/otp-widget-config")
      .then((r) => r.data.data),
  verifyPhone: (access_token: string) =>
    apiClient.post<ApiSuccess<{ phone_verified: boolean }>>("/auth/verify-phone/widget", { access_token }).then((r) => r.data.data),
  resetPassword: (payload: { access_token: string; phone: string; new_password: string }) =>
    apiClient.post("/auth/reset-password/widget", payload),
};

export const guestAuthApi = {
  bookingAccess: (phone: string) =>
    apiClient.post<ApiSuccess<{ mode: "register" | "otp" | "password" }>>("/auth/booking-access", { phone }).then((r) => r.data.data),
  otpLogin: (payload: { phone: string; otp?: string; access_token?: string }) =>
    apiClient.post<ApiSuccess<AuthResult>>("/auth/otp-login", payload).then((r) => r.data.data),
  setPassword: (new_password: string) => apiClient.post("/auth/set-password", { new_password }),
};

export const googleAuthApi = {
  config: () =>
    apiClient.get<ApiSuccess<{ enabled: boolean; client_id: string | null }>>("/auth/google-config").then((r) => r.data.data),
  login: (credential: string) =>
    apiClient.post<ApiSuccess<AuthResult>>("/auth/google", { credential }).then((r) => r.data.data),
  addPhoneRequest: (phone: string) => apiClient.post("/auth/add-phone/request", { phone }),
  addPhoneConfirm: (payload: { phone: string; otp?: string; access_token?: string }) =>
    apiClient.post("/auth/add-phone/confirm", payload),
};

export const mapsApi = {
  config: () =>
    apiClient.get<ApiSuccess<{ enabled: boolean; browser_key: string | null }>>("/auth/maps-config").then((r) => r.data.data),
};
