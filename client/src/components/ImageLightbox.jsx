// Simple full-screen image viewer: click the backdrop or the ✕ to close.
// Shared by the board, cast and props/backgrounds pages so a generated or
// uploaded image can be inspected at full size.
export default function ImageLightbox({ src, alt = "", onClose }) {
  if (!src) return null;
  return (
    <div className="lightbox-overlay" onClick={onClose}>
      {/* Wrapper shrinks to the image so the ✕ sits on its corner, not in the
          far corner of the screen. */}
      <div className="lightbox-figure" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="lightbox-close" onClick={onClose} title="Close">
          ✕
        </button>
        <img className="lightbox-img" src={src} alt={alt} />
      </div>
    </div>
  );
}
