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

  forgotPassword: (identifier: string) =>
    apiClient.post<ApiSuccess<{ otp_sent: boolean; debug_otp: string }>>("/auth/forgot-password", { identifier }).then((r) => r.data.data),

  resetPassword: (payload: { identifier: string; otp: string; new_password: string }) =>
    apiClient.post<ApiSuccess<null>>("/auth/reset-password", payload).then((r) => r.data),
};
