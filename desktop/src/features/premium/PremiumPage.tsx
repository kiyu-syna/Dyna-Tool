import { AlertTriangle, Check, CircleDollarSign, Clock3, Copy, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { formatDateTime, request } from "../../shared/api/client";
import { EmptyState, SkeletonRows, StatusPill } from "../../shared/components/Common";
import { useI18n } from "../../shared/i18n";
import type { LicensePlan, LicenseStatus, PaymentOrder, PaymentStatus } from "../../shared/types";
import "./premium.css";

function money(value: number, locale: string) {
  return new Intl.NumberFormat(locale).format(value) + " ₫";
}

export default function PremiumPage() {
  const [license, setLicense] = useState<LicenseStatus | null>(null);
  const [order, setOrder] = useState<PaymentOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [orderingDays, setOrderingDays] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const [qrError, setQrError] = useState(false);
  const [qrReload, setQrReload] = useState(0);
  const { locale, l } = useI18n();

  function planLabel(plan: LicensePlan) {
    if (plan.days === 365) return l(plan.label, "1 year", "1 年");
    return l(plan.label, `${plan.days} days`, `${plan.days} 天`);
  }

  function planRate(plan: LicensePlan) {
    return plan.per_day.replace("/ngày", l("/ngày", "/day", "/天"));
  }

  function planTag(tag?: string) {
    if (!tag) return tag;
    if (tag === "Phổ biến") return l(tag, "Popular", "热门");
    if (tag.startsWith("Tiết kiệm")) return tag.replace("Tiết kiệm", l("Tiết kiệm", "Save", "节省"));
    return tag;
  }

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

  useEffect(() => {
    void loadLicense(false);
  }, [loadLicense]);

  useEffect(() => {
    setQrError(false);
    setQrReload(0);
  }, [order?.qr_url]);

  useEffect(() => {
    if (!order || order.status !== "pending") return;
    let disposed = false;
    const poll = async () => {
      try {
        const status = await request<PaymentStatus>(`/api/license/orders/${encodeURIComponent(order.order_id)}`);
        if (disposed) return;
        setOrder((current) => (current ? { ...current, status: status.status } : current));
        if (status.status === "paid") {
          const verified = await request<{ is_active: boolean; info: LicenseStatus["info"] }>("/api/license/verify", {
            method: "POST",
          });
          if (!disposed) {
            setLicense((current) => ({
              is_active: verified.is_active,
              info: verified.info,
              plans: current?.plans || [],
            }));
          }
        }
      } catch (caught) {
        if (!disposed) setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    const timer = window.setInterval(() => void poll(), 4000);
    void poll();
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
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
  if (!license)
    return (
      <EmptyState
        error
        message={
          error ||
          l("Không tải được thông tin bản quyền.", "License information is unavailable.", "无法加载许可证信息。")
        }
      />
    );

  if (order)
    return (
      <div className="premium-payment page-stack">
        <section className="payment-header section">
          <div>
            <span className="payment-icon">
              <CircleDollarSign size={22} />
            </span>
            <div>
              <h2>{l("Thanh toán chuyển khoản", "Bank transfer payment", "银行转账付款")}</h2>
              <p>
                {order.plan_name} · {money(order.amount, locale)}
              </p>
            </div>
          </div>
          <button
            className="icon-button"
            title={l("Đóng đơn hàng", "Close order", "关闭订单")}
            onClick={() => setOrder(null)}
          >
            <X size={17} />
          </button>
        </section>
        {error && <div className="action-error">{error}</div>}
        <div className="payment-layout">
          <section className="section qr-section">
            <div className={`qr-frame ${qrError ? "error" : ""}`}>
              {qrError ? (
                <div className="qr-fallback">
                  <AlertTriangle size={24} />
                  <strong>{l("Không tải được mã QR", "Could not load the QR code", "无法加载二维码")}</strong>
                  <button
                    className="small-button"
                    onClick={() => {
                      setQrError(false);
                      setQrReload((value) => value + 1);
                    }}
                  >
                    <RefreshCw size={14} />
                    {l("Thử tải lại", "Retry", "重试")}
                  </button>
                </div>
              ) : (
                <img
                  src={`${order.qr_url}${order.qr_url.includes("?") ? "&" : "?"}dyna_reload=${qrReload}`}
                  alt={l("Mã QR thanh toán", "Payment QR code", "付款二维码")}
                  onError={() => setQrError(true)}
                />
              )}
            </div>
            <StatusPill
              text={
                order.status === "paid"
                  ? l("Đã thanh toán", "Paid", "已付款")
                  : order.status === "pending"
                    ? l("Đang chờ thanh toán", "Waiting for payment", "等待付款")
                    : order.status
              }
              tone={order.status === "paid" ? "success" : order.status === "pending" ? "warning" : "danger"}
            />
            <p>
              <Clock3 size={14} />
              {l("Đơn hết hạn", "Order expires", "订单到期")}: {formatDateTime(order.expires_at_order)}
            </p>
          </section>
          <section className="section transfer-details">
            <header>
              <h2>{l("Thông tin chuyển khoản", "Transfer details", "转账信息")}</h2>
              <span>{order.order_id}</span>
            </header>
            {[
              [l("Ngân hàng", "Bank", "银行"), order.bank_id, "bank"],
              [l("Số tài khoản", "Account number", "账号"), order.account_no, "account"],
              [l("Chủ tài khoản", "Account holder", "账户名"), order.account_name, "holder"],
              [l("Số tiền", "Amount", "金额"), money(order.amount, locale), "amount"],
              [l("Nội dung", "Transfer content", "转账附言"), order.transfer_content, "content"],
            ].map(([label, value, key]) => (
              <div className="transfer-row" key={key}>
                <span>{label}</span>
                <strong>{value}</strong>
                <button
                  className="icon-button"
                  onClick={() => void copy(value, key)}
                  title={l("Sao chép", "Copy", "复制")}
                >
                  {copied === key ? <Check size={15} /> : <Copy size={15} />}
                </button>
              </div>
            ))}
            <div className="payment-note">
              {l(
                "Vui lòng chuyển đúng số tiền và nội dung để hệ thống tự động kích hoạt.",
                "Use the exact amount and transfer content for automatic activation.",
                "请使用准确的金额和转账附言，系统将自动激活。",
              )}
            </div>
          </section>
        </div>
      </div>
    );

  return (
    <div className="premium-page page-stack">
      <section className="license-banner section">
        <div className={`license-emblem ${license.is_active ? "active" : ""}`}>
          <ShieldCheck size={24} />
        </div>
        <div>
          <span>{l("Trạng thái bản quyền", "License status", "许可证状态")}</span>
          <h2>
            {license.is_active
              ? license.info.plan_name || l("Premium đang hoạt động", "Premium active", "高级版已激活")
              : l("Chưa kích hoạt Premium", "Premium is not active", "高级版未激活")}
          </h2>
          <p>
            {license.is_active
              ? `${l("Còn lại", "Remaining", "剩余")} ${license.info.days_remaining ?? "-"} ${l("ngày", "days", "天")} · ${l("Hết hạn", "Expires", "到期")} ${formatDateTime(license.info.expires_at)}`
              : l(
                  "Chọn gói phù hợp để mở quyền sử dụng.",
                  "Select a plan to activate your license.",
                  "请选择合适的方案以激活许可证。",
                )}
          </p>
        </div>
        <button className="secondary-button" disabled={refreshing} onClick={() => void loadLicense(true)}>
          <RefreshCw size={15} className={refreshing ? "spin" : ""} />
          {l("Kiểm tra lại", "Refresh", "刷新")}
        </button>
      </section>
      {error && <div className="action-error">{error}</div>}
      <section className="section plan-section">
        <header className="section-header">
          <h2>{l("Gói sử dụng", "Subscription plans", "订阅方案")}</h2>
          <span>
            {l(
              "Thanh toán một lần, không tự gia hạn",
              "One-time payment, no automatic renewal",
              "一次性付款，不自动续订",
            )}
          </span>
        </header>
        <div className="plan-grid">
          {license.plans.map((plan) => (
            <article className={plan.tag ? "featured" : ""} key={plan.days}>
              {plan.tag && <span className="plan-tag">{planTag(plan.tag)}</span>}
              <h3>{planLabel(plan)}</h3>
              <strong>{money(plan.price, locale)}</strong>
              <p>{planRate(plan)}</p>
              <button
                className="primary-button"
                disabled={orderingDays !== null}
                onClick={() => void createOrder(plan)}
              >
                {orderingDays === plan.days
                  ? l("Đang tạo đơn...", "Creating...", "正在创建订单...")
                  : l("Chọn gói", "Select plan", "选择方案")}
              </button>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
