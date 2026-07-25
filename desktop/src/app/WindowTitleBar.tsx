import appMark from "../assets/dyna-mark.png";

export default function WindowTitleBar() {
  return (
    <div className="window-titlebar">
      <div className="window-title-copy">
        <img src={appMark} alt="" />
        <span>Dyna</span>
      </div>
      <div className="window-controls">
        <button aria-label="Thu nhỏ" onClick={() => void window.dyna?.windowControl("minimize")}>
          −
        </button>
        <button aria-label="Phóng to" onClick={() => void window.dyna?.windowControl("maximize")}>
          □
        </button>
        <button className="window-close" aria-label="Đóng" onClick={() => void window.dyna?.windowControl("close")}>
          ×
        </button>
      </div>
    </div>
  );
}
