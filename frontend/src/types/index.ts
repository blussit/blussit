export type UserRole = "customer" | "captain" | "manager" | "admin";
export type UserStatus = "active" | "inactive" | "suspended" | "pending";
// An id referencing a VehicleTypeOption — admin-managed (Hatchback/Sedan/SUV/...),
// not a fixed set, so a plain string rather than a union of literal values.
export type VehicleType = string;

export interface VehicleTypeOption {
  id: string;
  name: string;
  slug: string;
  display_order: number;
  is_active: boolean;
}

export interface User {
  id: string;
  full_name: string;
  email?: string | null;
  phone?: string | null;
  role: UserRole;
  status: UserStatus;
  profile_image?: string | null;
  service_center_id?: string | null;
  must_change_password?: boolean;
  created_at: string;
}

export interface Vehicle {
  id: string;
  owner_id: string;
  vehicle_type: VehicleType;
  brand: string;
  model: string;
  registration_number: string;
  color?: string | null;
  image?: string | null;
  is_default: boolean;
}

export interface Address {
  id: string;
  owner_id: string;
  label: string;
  line1: string;
  line2?: string | null;
  landmark?: string | null;
  city: string;
  state: string;
  pincode: string;
  latitude?: number | null;
  longitude?: number | null;
  is_default: boolean;
}

export interface Category {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  icon?: string | null;
  is_active: boolean;
}

export interface Service {
  id: string;
  category_id: string;
  name: string;
  slug: string;
  description?: string | null;
  vehicle_types: VehicleType[];
  price: number;
  discounted_price?: number | null;
  vehicle_type_prices?: Record<string, number>;
  vehicle_type_discounted_prices?: Record<string, number>;
  duration_minutes: number;
  image?: string | null;
  is_active: boolean;
  is_featured: boolean;
  captain_fee?: number | null;
}

export interface ComboOffer {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  service_ids: string[];
  vehicle_types: VehicleType[];
  price: number;
  discounted_price?: number | null;
  vehicle_type_prices?: Record<string, number>;
  vehicle_type_discounted_prices?: Record<string, number>;
  image?: string | null;
  is_active: boolean;
  is_featured: boolean;
}

export interface BookingPolicy {
  operating_start: string;
  operating_end: string;
  min_lead_minutes: number;
  slot_granularity_minutes: number;
  captain_travel_buffer_minutes: number;
  photo_geofence_radius_m: number;
  late_start_grace_minutes: number;
  wallet_gating_enabled: boolean;
}

export interface HomepageConfig {
  hero_badge_text: string;
  hero_headline: string;
  hero_subtext: string;
  featured_service_id: string | null;
  featured_combo_ids: string[];
  banner_active: boolean;
  banner_text: string;
}

export interface ContactMessage {
  id: string;
  name: string;
  phone: string;
  email: string;
  message: string;
  created_at: string;
}

export type BookingStatus =
  | "pending"
  | "assigned"
  | "captain_on_the_way"
  | "service_started"
  | "completed"
  | "cancelled"
  | "rescheduled";

export interface GeoPoint {
  latitude: number;
  longitude: number;
}

export interface PhotoCapture {
  image_url: string;
  latitude: number;
  longitude: number;
  captured_at: string;
}

export interface EquipmentUsed {
  inventory_item_id: string;
  item_name: string;
  quantity: number;
}

export interface Booking {
  id: string;
  booking_number: string;
  customer_id: string;
  vehicle_id: string;
  address_id: string;
  service_center_id: string;
  captain_id?: string | null;
  service_ids: string[];
  subscription_id?: string | null;
  scheduled_date: string;
  scheduled_slot: string;
  duration_minutes?: number;
  actual_duration_minutes?: number | null;
  status: BookingStatus;
  payment_status: string;
  payment_method: string;
  subtotal: number;
  discount_amount: number;
  tax_amount: number;
  total_amount: number;
  vehicle_registration_number?: string | null;
  vehicle_verified?: boolean;
  vehicle_verified_at?: string | null;
  captain_start_stage?: "early" | "on_time" | "late" | "severely_late" | null;
  late_penalty_pct?: number;
  issue_flag?: "captain_not_reached" | "captain_delay" | string | null;
  issue_notes?: string | null;
  issue_flagged_at?: string | null;
  issue_resolved?: boolean;
  before_photo_flagged?: boolean;
  before_photo_distance_m?: number | null;
  after_photo_flagged?: boolean;
  after_photo_distance_m?: number | null;
  captain_earning?: number | null;
  platform_earning?: number | null;
  wallet_settled?: boolean;
  coupon_code?: string | null;
  customer_notes?: string | null;
  alternate_contact_name?: string | null;
  alternate_contact_phone?: string | null;
  cancellation_reason?: string | null;
  is_rated: boolean;
  created_at: string;
  heading_at?: string | null;
  heading_location?: GeoPoint | null;
  equipment_used?: EquipmentUsed[];
  before_photo?: PhotoCapture | null;
  after_photo?: PhotoCapture | null;
  service_started_at?: string | null;
  completed_at?: string | null;
  status_history?: { status: string; note?: string; created_at: string }[];
  // Denormalized onto the response for display — see BookingService._enrich_bookings.
  customer_name?: string | null;
  customer_phone?: string | null;
  vehicle_snapshot?: {
    vehicle_type: VehicleType;
    brand: string;
    model: string;
    registration_number: string;
  } | null;
  address_snapshot?: {
    line1: string;
    landmark?: string | null;
    city: string;
    state: string;
    pincode: string;
    latitude?: number | null;
    longitude?: number | null;
  } | null;
  service_names?: string[] | null;
  combo_name?: string | null;
}

export interface CaptainWallet {
  id: string;
  captain_id: string;
  balance: number;
  minimum_balance: number;
  bank_account_number?: string | null;
  bank_ifsc?: string | null;
  bank_account_holder?: string | null;
  updated_at: string;
}

export interface WalletTransaction {
  id: string;
  captain_id: string;
  booking_id?: string | null;
  type: "credit" | "debit";
  amount: number;
  balance_after: number;
  description: string;
  created_at: string;
}

export interface WithdrawalRequest {
  id: string;
  captain_id: string;
  amount: number;
  status: "pending" | "approved" | "rejected" | "paid";
  review_note?: string | null;
  reviewed_by?: string | null;
  created_at: string;
}

export interface PricingConfig {
  per_km_rate: number;
  default_captain_service_fee: number;
  updated_at?: string;
}

export interface SubscriptionPlan {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  billing_cycle: "monthly" | "quarterly" | "yearly";
  price: number;
  discounted_price?: number | null;
  vehicle_type_prices?: Record<string, number>;
  vehicle_type_discounted_prices?: Record<string, number>;
  category_quotas?: Record<string, number>;
  // Which specific services this plan covers — when set, a booking made
  // from this subscription uses exactly these, no picking required. Empty
  // = an unrestricted/legacy plan (any service, up to the flat count).
  included_service_ids?: string[];
  total_service_count: number;
  vehicle_types: VehicleType[];
  upgrade_to_plan_ids?: string[];
  is_active: boolean;
  is_popular: boolean;
}

export interface UserSubscription {
  id: string;
  customer_id: string;
  plan_id: string;
  vehicle_id: string;
  status: "active" | "expired" | "cancelled" | "paused";
  // Computed at read time — "expired" whenever end_date has passed, even if
  // the stored `status` field hasn't been lazily flipped yet (only actually
  // using a subscription triggers that write). Prefer this over `status`
  // for any "is this actually usable right now" display/filtering.
  effective_status: "active" | "expired" | "cancelled" | "paused";
  total_service_count: number;
  remaining_service_count: number;
  total_by_category?: Record<string, number>;
  remaining_by_category?: Record<string, number>;
  start_date: string;
  end_date: string;
}

export interface ServiceCenter {
  id: string;
  name: string;
  code: string;
  location: {
    address: string;
    city: string;
    state: string;
    pincode: string;
    latitude?: number | null;
    longitude?: number | null;
    service_pincodes: string[];
    radius_km: number;
  };
  manager_id?: string | null;
  contact_phone?: string | null;
  contact_email?: string | null;
  working_hours_start?: string;
  working_hours_end?: string;
  is_active: boolean;
}

export interface Complaint {
  id: string;
  customer_id: string;
  booking_id?: string | null;
  service_center_id?: string | null;
  subject: string;
  description: string;
  priority: "low" | "medium" | "high" | "urgent";
  status: "open" | "in_progress" | "resolved" | "closed";
  created_at: string;
}

export interface Review {
  id: string;
  booking_id: string;
  customer_id: string;
  captain_id?: string | null;
  rating: number;
  comment?: string | null;
  created_at: string;
}

export interface Notification {
  id: string;
  user_id: string;
  title: string;
  message: string;
  notification_type: string;
  reference_id?: string | null;
  is_read: boolean;
  created_at: string;
}

export interface InventoryItem {
  id: string;
  service_center_id: string;
  item_name: string;
  unit: string;
  quantity_available: number;
  reorder_level: number;
}

export interface Coupon {
  id: string;
  code: string;
  description?: string | null;
  coupon_type: "flat" | "percentage";
  value: number;
  min_order_value: number;
  is_active: boolean;
  valid_from: string;
  valid_until: string;
}
