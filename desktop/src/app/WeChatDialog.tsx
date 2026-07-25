import { X } from "lucide-react";
import wechatQr from "../assets/wechat-qr.png";
import { useI18n } from "../shared/i18n";

export default function WeChatDialog({ onClose }: { onClose(): void }) {
  const { l } = useI18n();

  return (
    <div className="modal-backdrop wechat-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="wechat-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wechat-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <h2 id="wechat-dialog-title">{l("Liên hệ qua WeChat", "Contact via WeChat", "通过微信联系")}</h2>
            <p>
              {l(
                "Quét mã để thêm bạn và gửi góp ý.",
                "Scan the code to add a friend and send feedback.",
                "扫描二维码添加好友并发送反馈。",
              )}
            </p>
          </div>
          <button className="icon-button" onClick={onClose} title={l("Đóng", "Close", "关闭")}>
            <X size={18} />
          </button>
        </header>
        <div className="wechat-qr-wrap">
          <img src={wechatQr} alt={l("Mã QR WeChat", "WeChat QR code", "微信二维码")} />
        </div>
      </section>
    </div>
  );
}
