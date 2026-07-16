import { AlertTriangle, Check, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../api";
import { EmptyState, Modal, PlatformMarks, SkeletonRows } from "../components/Common";
import { usePolling } from "../hooks";
import type { PlatformKey, ProfileConfig, ProfileSummary } from "../types";

const platforms: Array<[PlatformKey, string]> = [
  ["tiktok", "TikTok"], ["youtube", "YouTube Shorts"], ["facebook", "Facebook Reels"],
];

export default function ProfilesPage() {
  const summaries = usePolling<{ profiles: ProfileSummary[] }>("/api/profiles", 10000);
  const [selectedId, setSelectedId] = useState("");
  const [profile, setProfile] = useState<ProfileConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [messageError, setMessageError] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createId, setCreateId] = useState("");
  const [createName, setCreateName] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);

  useEffect(() => {
    if (!selectedId && summaries.data?.profiles.length) setSelectedId(summaries.data.profiles[0].id);
  }, [selectedId, summaries.data]);

  useEffect(() => {
    if (!selectedId) return;
    setProfile(null);
    setMessage("");
    void request<{ profile: ProfileConfig }>(`/api/profiles/${selectedId}`)
      .then((result) => setProfile(result.profile))
      .catch((error) => setMessage(error instanceof Error ? error.message : String(error)));
  }, [selectedId]);

  function update(next: Partial<ProfileConfig>) {
    setProfile((current) => current ? { ...current, ...next } : current);
  }

  function updatePlatform(key: PlatformKey, field: string, value: unknown) {
    setProfile((current) => current ? {
      ...current,
      [key]: { ...(current[key] || {}), [field]: value },
    } : current);
  }

  async function save() {
    if (!profile) return;
    setSaving(true);
    setMessage("");
    try {
      const result = await request<{ profile: ProfileConfig }>(`/api/profiles/${profile.id}`, {
        method: "PUT", body: { profile },
      });
      setProfile(result.profile);
      setMessage("Đã lưu cấu hình Profile");
      setMessageError(false);
      await summaries.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      setMessageError(true);
    } finally {
      setSaving(false);
    }
  }

  async function createProfile() {
    setSaving(true);
    try {
      const result = await request<{ profile: ProfileConfig }>("/api/profiles", {
        method: "POST",
        body: { id: createId, name: createName },
      });
      await summaries.refresh();
      setSelectedId(result.profile.id);
      setCreateOpen(false);
      setCreateId("");
      setCreateName("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      setMessageError(true);
    } finally {
      setSaving(false);
    }
  }

  async function deleteProfile() {
    if (!profile) return;
    setSaving(true);
    try {
      await request(`/api/profiles/${encodeURIComponent(profile.id)}`, { method: "DELETE" });
      const nextId = summaries.data?.profiles.find((item) => item.id !== profile.id)?.id || "";
      setDeleteOpen(false);
      setProfile(null);
      setSelectedId(nextId);
      await summaries.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      setMessageError(true);
      setDeleteOpen(false);
    } finally {
      setSaving(false);
    }
  }

  const sources = profile?.douyin?.sources || [];
  function updateSource(index: number, field: string, value: unknown) {
    if (!profile) return;
    const next = sources.map((source, sourceIndex) => sourceIndex === index ? { ...source, [field]: value } : source);
    update({ douyin: { ...(profile.douyin || {}), sources: next } });
  }

  function addSource() {
    if (!profile) return;
    update({ douyin: { ...(profile.douyin || {}), sources: [...sources, { target_sec_uid: "", target_display_name: "", enabled: true }] } });
  }

  function removeSource(index: number) {
    if (!profile) return;
    update({ douyin: { ...(profile.douyin || {}), sources: sources.filter((_, sourceIndex) => sourceIndex !== index) } });
  }

  if (summaries.loading && !summaries.data) return <SkeletonRows count={8} />;
  if (summaries.error && !summaries.data) return <EmptyState error message={summaries.error} />;

  return (
    <div className="profiles-layout">
      <aside className="profile-picker">
        <header><h2>Profile</h2><div className="inline-actions"><span>{summaries.data?.profiles.length || 0}</span><button className="icon-button" onClick={() => setCreateOpen(true)} title="Tạo Profile"><Plus size={15} /></button></div></header>
        <div>{summaries.data?.profiles.map((item) => (
          <button key={item.id} className={selectedId === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}>
            <b>{item.id}</b><span><strong>{item.name}</strong><small>{item.source_count} nguồn · {item.check_interval_minutes} phút</small></span>
            <PlatformMarks platforms={item.platforms} />
          </button>
        ))}</div>
      </aside>

      <div className="profile-editor">
        {!profile ? <SkeletonRows count={7} /> : <>
          <div className="editor-toolbar">
            <div><h2>{profile.name}</h2><p>Profile ID {profile.id}</p></div>
            <div className="toolbar-actions">
              {message && <span className={messageError ? "save-message error" : "save-message"}>{messageError ? <AlertTriangle size={14} /> : <Check size={14} />}{message}</span>}
              <button className="icon-button danger" onClick={() => setDeleteOpen(true)} title="Xóa Profile"><Trash2 size={16} /></button>
              <button className="primary-button" onClick={() => void save()} disabled={saving}><Save size={16} />{saving ? "Đang lưu" : "Lưu cấu hình"}</button>
            </div>
          </div>

          <section className="form-section"><h3>Thông tin chung</h3><div className="form-grid">
            <label><span>Tên Profile</span><input value={profile.name} onChange={(event) => update({ name: event.target.value })} /></label>
            <label><span>Chu kỳ kiểm tra chung (phút)</span><input type="number" min="1" value={profile.check_interval_minutes || 30} onChange={(event) => update({ check_interval_minutes: Number(event.target.value) })} /></label>
            <label><span>Độ ưu tiên xử lý (0 là cao nhất)</span><input type="number" min="0" max="1000" value={profile.processing_priority ?? 100} onChange={(event) => update({ processing_priority: Number(event.target.value) })} /></label>
            <label className="full"><span>Thư mục lưu video</span><input value={profile.save_dir || ""} onChange={(event) => update({ save_dir: event.target.value })} /></label>
            <label className="full"><span>Mô tả mặc định gồm hashtag</span><textarea rows={3} value={profile.default_caption || ""} onChange={(event) => update({ default_caption: event.target.value })} /></label>
            <label className="switch-row"><input type="checkbox" checked={profile.enabled} onChange={(event) => update({ enabled: event.target.checked })} /><span>Kích hoạt Profile</span></label>
          </div></section>

          <section className="form-section"><h3>Bộ lọc video</h3><div className="form-grid">
            <label><span>Lượt tim tối thiểu</span><input type="number" min="0" value={profile.filters?.min_likes || 0} onChange={(event) => update({ filters: { ...(profile.filters || {}), min_likes: Number(event.target.value) } })} /></label>
            <label><span>Độ dài tối đa (giây)</span><input type="number" min="1" value={profile.filters?.max_duration_seconds || 120} onChange={(event) => update({ filters: { ...(profile.filters || {}), max_duration_seconds: Number(event.target.value) } })} /></label>
          </div></section>

          <section className="form-section"><div className="form-section-title"><h3>Nguồn Douyin</h3><button className="secondary-button" onClick={addSource}><Plus size={15} />Thêm nguồn</button></div>
            <div className="form-grid compact-top"><label className="full"><span>GemLogin Profile ID dùng để đọc Douyin</span><input value={profile.douyin?.gemlogin_profile_id || profile.id} onChange={(event) => update({ douyin: { ...(profile.douyin || {}), gemlogin_profile_id: event.target.value } })} /></label></div>
            <div className="source-list">{sources.map((source, index) => (
              <div className="source-row" key={index}>
                <b>{String(index + 1).padStart(2, "0")}</b>
                <input placeholder="Tên gợi nhớ" value={source.target_display_name || ""} onChange={(event) => updateSource(index, "target_display_name", event.target.value)} />
                <input className="mono" placeholder="sec_uid" value={source.target_sec_uid} onChange={(event) => updateSource(index, "target_sec_uid", event.target.value)} />
                <label className="compact-check"><input type="checkbox" checked={source.enabled !== false} onChange={(event) => updateSource(index, "enabled", event.target.checked)} /><span>Bật</span></label>
                <button className="icon-button danger" onClick={() => removeSource(index)} title="Xóa nguồn"><Trash2 size={15} /></button>
              </div>
            ))}</div>
          </section>

          <section className="form-section"><h3>Nền tảng đăng</h3><div className="platform-config-grid">{platforms.map(([key, label]) => (
            <div className="platform-config" key={key}>
              <label className="switch-row"><input type="checkbox" checked={profile[key]?.enabled === true} onChange={(event) => updatePlatform(key, "enabled", event.target.checked)} /><span>{label}</span></label>
              <label><span>GemLogin Profile ID</span><input value={String(profile[key]?.gemlogin_profile_id || profile.id)} onChange={(event) => updatePlatform(key, "gemlogin_profile_id", event.target.value)} /></label>
              <label className="switch-row"><input type="checkbox" checked={profile[key]?.use_original_desc === true} onChange={(event) => updatePlatform(key, "use_original_desc", event.target.checked)} /><span>Dùng mô tả gốc</span></label>
              {key === "youtube" && <label><span>Channel ID</span><input value={profile.youtube?.channel_id || ""} onChange={(event) => updatePlatform("youtube", "channel_id", event.target.value)} /></label>}
              {key === "youtube" && <label><span>Preset FFmpeg</span><select value={profile.youtube?.preset || "slow"} onChange={(event) => updatePlatform("youtube", "preset", event.target.value)}>{["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"].map((preset) => <option key={preset}>{preset}</option>)}</select></label>}
              {key === "youtube" && <label><span>CRF (thấp hơn = đẹp hơn)</span><input type="number" min="0" max="51" value={profile.youtube?.crf ?? 16} onChange={(event) => updatePlatform("youtube", "crf", Number(event.target.value))} /></label>}
              {key === "facebook" && <label><span>URL trang cá nhân</span><input value={profile.facebook?.profile_url || ""} onChange={(event) => updatePlatform("facebook", "profile_url", event.target.value)} /></label>}
            </div>
          ))}</div></section>
        </>}
      </div>
      {createOpen && <Modal title="Tạo Profile" onClose={() => setCreateOpen(false)} footer={<><button className="secondary-button" onClick={() => setCreateOpen(false)}>Hủy</button><button className="primary-button" disabled={saving || !createId.trim()} onClick={() => void createProfile()}><Plus size={15} />Tạo Profile</button></>}>
        <div className="form-grid"><label><span>Profile ID</span><input autoFocus inputMode="numeric" value={createId} onChange={(event) => setCreateId(event.target.value.replace(/\D/g, ""))} placeholder="Ví dụ: 6" /></label><label><span>Tên Profile</span><input value={createName} onChange={(event) => setCreateName(event.target.value)} placeholder="Tự điền nếu để trống" /></label></div>
      </Modal>}
      {deleteOpen && profile && <Modal title="Xóa Profile" onClose={() => setDeleteOpen(false)} footer={<><button className="secondary-button" onClick={() => setDeleteOpen(false)}>Giữ lại</button><button className="small-button danger destructive" disabled={saving} onClick={() => void deleteProfile()}><Trash2 size={15} />Xóa Profile</button></>}>
        <div className="confirm-copy"><AlertTriangle size={20} /><div><strong>Xóa Profile {profile.id} - {profile.name}?</strong><p>Cấu hình Profile sẽ bị xóa. Lịch sử hàng đợi và dữ liệu đối chiếu được giữ lại để phục hồi khi cần.</p></div></div>
      </Modal>}
    </div>
  );
}
