export type AuthUser = {
  username: string;
  phone?: string;
  display_name?: string;
};

export type AuthStatus = {
  authenticated: boolean;
  user: AuthUser | null;
};

export type LicensePlan = {
  days: number;
  label: string;
  price: number;
  per_day: string;
  tag?: string;
};

export type LicenseInfo = {
  username?: string;
  is_active?: boolean;
  plan_name?: string;
  expires_at?: string;
  days_remaining?: number;
  source?: string;
  error?: string;
};

export type LicenseStatus = {
  is_active: boolean;
  info: LicenseInfo;
  plans: LicensePlan[];
};

export type PaymentOrder = {
  order_id: string;
  username: string;
  days: number;
  plan_name: string;
  amount: number;
  transfer_content: string;
  qr_url: string;
  bank_id: string;
  account_no: string;
  account_name: string;
  expires_at_order: string;
  status: "pending" | "paid" | "expired" | "cancelled" | string;
};

export type PaymentStatus = {
  order_id: string;
  status: "pending" | "paid" | "expired" | "cancelled" | string;
  amount: number;
  paid_at?: string | null;
  subscription_expires_at?: string | null;
  plan_name: string;
};
