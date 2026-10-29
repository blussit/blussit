/** Admin content manager — FAQs and customer testimonials. Server contract:
 *  backend/app/routes/v1/content_routes.py. Each call returns the whole
 *  envelope so pages can toast the SERVER's message. */
import { apiClient, type ApiSuccess } from "../lib/api-client";

export interface AdminFaq {
  id: string;
  question: string;
  answer: string;
  display_order: number;
  is_active: boolean;
}

export interface AdminTestimonial {
  id: string;
  customer_name: string;
  customer_image?: string | null;
  rating: number;
  comment: string;
  is_featured: boolean;
  display_order: number;
}

export type FaqInput = Pick<AdminFaq, "question" | "answer"> & Partial<Pick<AdminFaq, "display_order" | "is_active">>;
export type FaqChange = Partial<FaqInput>;
export type TestimonialInput = Pick<AdminTestimonial, "customer_name" | "comment" | "rating"> &
  Partial<Pick<AdminTestimonial, "customer_image" | "is_featured" | "display_order">>;
export type TestimonialChange = Partial<TestimonialInput>;

/** Limits enforced by the server (schemas/content_schema.py) — the forms count against the same numbers. */
export const CONTENT_LIMITS = {
  questionMin: 5,
  questionMax: 200,
  answerMin: 5,
  answerMax: 1500,
  nameMin: 2,
  nameMax: 80,
  commentMin: 5,
  commentMax: 600,
} as const;

export const adminFaqApi = {
  /** `active_only=false` asks for hidden rows too (admin/manager token only). */
  list: () => apiClient.get<ApiSuccess<AdminFaq[]>>("/faqs", { params: { active_only: false } }).then((r) => r.data.data),
  create: (payload: FaqInput) => apiClient.post<ApiSuccess<AdminFaq>>("/faqs", payload).then((r) => r.data),
  update: (id: string, payload: FaqChange) => apiClient.put<ApiSuccess<AdminFaq>>(`/faqs/${id}`, payload).then((r) => r.data),
  remove: (id: string) => apiClient.delete<ApiSuccess<null>>(`/faqs/${id}`).then((r) => r.data),
};

export const adminTestimonialApi = {
  list: () => apiClient.get<ApiSuccess<AdminTestimonial[]>>("/testimonials", { params: { featured_only: false } }).then((r) => r.data.data),
  create: (payload: TestimonialInput) => apiClient.post<ApiSuccess<AdminTestimonial>>("/testimonials", payload).then((r) => r.data),
  update: (id: string, payload: TestimonialChange) =>
    apiClient.put<ApiSuccess<AdminTestimonial>>(`/testimonials/${id}`, payload).then((r) => r.data),
  remove: (id: string) => apiClient.delete<ApiSuccess<null>>(`/testimonials/${id}`).then((r) => r.data),
};
