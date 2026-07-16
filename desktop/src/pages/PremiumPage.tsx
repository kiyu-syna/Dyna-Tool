import { Check, CircleDollarSign, Clock3, Copy, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { formatDateTime, request } from "../api";
import { EmptyState, SkeletonRows, StatusPill } from "../components/Common";
import type { LicensePlan, LicenseStatus, PaymentOrder, PaymentStatus } from "../types";

function money(value: number) {
  return new Intl.NumberFormat("vi-VN").format(value) + "đ";
}

export default function PremiumPage({ language }: { language: "vi" | "en" }) {
  const [license, setLicense] = useState<LicenseStatus | null>(null);
  const [order, setOrder] = useState<PaymentOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [orderingDays, setOrderingDays] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const vi = language === "vi";

  const loadLicense = useCallback(async (refresh = false) => {
    setRefreshing(refresh);
    try {
      const result = await request<LicenseStatus>(`/api/license?refresh=${refresh ? "true" : "false"}`);
      setLicense(result);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void loadLicense(false); }, [loadLicense]);

  useEffect(() => {
    if (!order || order.status !== "pending") return;
    let disposed = false;
    const poll = async () => {
      try {
        const status = await request<PaymentStatus>(`/api/license/orders/${encodeURIComponent(order.order_id)}`);
        if (disposed) return;
        setOrder((current) => current ? { ...current, status: status.status } : current);
        if (status.status === "paid") {
          const verified = await request<{ is_active: boolean; info: LicenseStatus["info"] }>("/api/license/verify", { method: "POST" });
          if (!disposed) {
            setLicense((current) => ({ is_active: verified.is_active, info: verified.info, plans: current?.plans || [] }));
          }
        }
      } catch (caught) {
        if (!disposed) setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    const timer = window.setInterval(() => void poll(), 4000);
    void poll();
    return () => { disposed = true; window.clearInterval(timer); };
  }, [order?.order_id, order?.status]);

  async function createOrder(plan: LicensePlan) {
    setOrderingDays(plan.days);
    setError("");
    try {
      const result = await request<PaymentOrder>("/api/license/orders", { method: "POST", body: { days: plan.days } });
      setOrder(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setOrderingDays(null);
    }
  }

  async function copy(value: string, label: string) {
    await navigator.clipboard.writeText(value);
    setCopied(label);
    window.setTimeout(() => setCopied(""), 1400);
  }

  if (loading) return <SkeletonRows count={6} />;
  if (!license) return <EmptyState error message={error || (vi ? "Không tải được thông tin bản quyền." : "License information is unavailable.")} />;

  if (order) return <div className="premium-payment page-stack">
    <section className="payment-header section">
      <div>
        <span className="payment-icon"><CircleDollarSign size={22} /></span>
        <div><h2>{vi ? "Thanh toán chuyển khoản" : "Bank transfer payment"}</h2><p>{order.plan_name} · {money(order.amount)}</p></div>
      </div>
      <button className="icon-button" title={vi ? "Đóng đơn hàng" : "Close order"} onClick={() => setOrder(null)}><X size={17} /></button>
    </section>
    {error && <div className="action-error">{error}</div>}
    <div className="payment-layout">
      <section className="section qr-section">
        <div className="qr-frame"><img src={order.qr_url} alt={vi ? "Mã QR thanh toán" : "Payment QR code"} /></div>
        <StatusPill text={order.status === "paid" ? (vi ? "Đã thanh toán" : "Paid") : order.status === "pending" ? (vi ? "Đang chờ thanh toán" : "Waiting for payment") : order.status} tone={order.status === "paid" ? "success" : order.status === "pending" ? "warning" : "danger"} />
        <p><Clock3 size={14} />{vi ? "Đơn hết hạn" : "Order expires"}: {formatDateTime(order.expires_at_order)}</p>
      </section>
      <section className="section transfer-details">
        <header><h2>{vi ? "Thông tin chuyển khoản" : "Transfer details"}</h2><span>{order.order_id}</span></header>
        {[
          [vi ? "Ngân hàng" : "Bank", order.bank_id, "bank"],
          [vi ? "Số tài khoản" : "Account number", order.account_no, "account"],
          [vi ? "Chủ tài khoản" : "Account holder", order.account_name, "holder"],
          [vi ? "Số tiền" : "Amount", money(order.amount), "amount"],
          [vi ? "Nội dung" : "Transfer content", order.transfer_content, "content"],
        ].map(([label, value, key]) => <div className="transfer-row" key={key}><span>{label}</span><strong>{value}</strong><button className="icon-button" onClick={() => void copy(value, key)} title={vi ? "Sao chép" : "Copy"}>{copied === key ? <Check size={15} /> : <Copy size={15} />}</button></div>)}
        <div className="payment-note">{vi ? "Vui lòng chuyển đúng số tiền và nội dung để hệ thống tự động kích hoạt." : "Use the exact amount and transfer content for automatic activation."}</div>
      </section>
    </div>
  </div>;

  return <div className="premium-page page-stack">
    <section className="license-banner section">
      <div className={`license-emblem ${license.is_active ? "active" : ""}`}><ShieldCheck size={24} /></div>
      <div><span>{vi ? "Trạng thái bản quyền" : "License status"}</span><h2>{license.is_active ? (license.info.plan_name || (vi ? "Premium đang hoạt động" : "Premium active")) : (vi ? "Chưa kích hoạt Premium" : "Premium is not active")}</h2><p>{license.is_active ? `${vi ? "Còn lại" : "Remaining"} ${license.info.days_remaining ?? "-"} ${vi ? "ngày" : "days"} · ${vi ? "Hết hạn" : "Expires"} ${formatDateTime(license.info.expires_at)}` : (vi ? "Chọn gói phù hợp để mở quyền sử dụng." : "Select a plan to activate your license.")}</p></div>
      <button className="secondary-button" disabled={refreshing} onClick={() => void loadLicense(true)}><RefreshCw size={15} className={refreshing ? "spin" : ""} />{vi ? "Kiểm tra lại" : "Refresh"}</button>
    </section>
    {error && <div className="action-error">{error}</div>}
    <section className="section plan-section">
      <header className="section-header"><h2>{vi ? "Gói sử dụng" : "Subscription plans"}</h2><span>{vi ? "Thanh toán một lần, không tự gia hạn" : "One-time payment, no automatic renewal"}</span></header>
      <div className="plan-grid">{license.plans.map((plan) => <article className={plan.tag ? "featured" : ""} key={plan.days}>
        {plan.tag && <span className="plan-tag">{plan.tag}</span>}
        <h3>{plan.label}</h3><strong>{money(plan.price)}</strong><p>{plan.per_day}</p>
        <button className="primary-button" disabled={orderingDays !== null} onClick={() => void createOrder(plan)}>{orderingDays === plan.days ? (vi ? "Đang tạo đơn..." : "Creating...") : (vi ? "Chọn gói" : "Select plan")}</button>
      </article>)}</div>
    </section>
  </div>;
}
