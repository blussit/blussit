import { useEffect, useState } from "react";
import { API_BASE_URL, apiClient, getErrorMessage } from "../../lib/api-client";
import { Modal, Spinner } from "../ui";

/**
 * KYC documents live in a PRIVATE bucket behind GET /uploads/document/…,
 * which needs the staff member's login header — a plain <a href>/<img src>
 * can't send it, so in production every "View Doc" answered 401. These are
 * fetched through the API client (header attached, session refreshed) as a
 * blob and shown from a short-lived object URL, revoked when done.
 *
 * Only our own API's document route is fetched this way — the token is
 * never sent anywhere else; any other URL (local-dev static files, public
 * photos) is used as is.
 */
export function isPrivateDocumentUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const target = new URL(url, window.location.origin);
    const api = new URL(API_BASE_URL, window.location.origin);
    return target.origin === api.origin && target.pathname.includes("/uploads/document/");
  } catch {
    return false;
  }
}

/** An object URL for `url` while `enabled` (revoked on change/unmount). */
export function usePrivateFile(url: string | null | undefined, enabled = true) {
  const [state, setState] = useState<{ src: string | null; type: string; loading: boolean; error: string }>({ src: null, type: "", loading: false, error: "" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!url || !enabled) {
      setState({ src: null, type: "", loading: false, error: "" });
      return;
    }
    if (!isPrivateDocumentUrl(url)) {
      setState({ src: url, type: "", loading: false, error: "" });
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ src: null, type: "", loading: true, error: "" });
    apiClient
      .get<Blob>(url, { responseType: "blob", baseURL: "" })
      .then((r) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(r.data);
        setState({ src: objectUrl, type: r.data.type || "", loading: false, error: "" });
      })
      .catch((err) => {
        if (!cancelled) setState({ src: null, type: "", loading: false, error: getErrorMessage(err) });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url, enabled, attempt]);
  return { ...state, retry: () => setAttempt((n) => n + 1) };
}

/** The document itself: an image inline, anything else (a PDF) as a link. */
export function PrivateDocumentView({ url, label, className = "" }: { url: string; label: string; className?: string }) {
  const file = usePrivateFile(url);
  if (file.loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (file.error) {
    return (
      <p role="alert" className="text-sm text-[#5F6878]">
        Couldn't open this document. {file.error}{" "}
        <button type="button" onClick={file.retry} className="font-semibold text-[#0A66F0] hover:underline">
          Try Again
        </button>
      </p>
    );
  }
  if (!file.src) return null;
  const isImage = !file.type || file.type.startsWith("image/");
  return isImage ? (
    <img src={file.src} alt={label} className={className || "max-h-[60vh] w-full rounded-xl bg-[#F6F8FC] object-contain"} />
  ) : (
    <a href={file.src} target="_blank" rel="noreferrer" download={label} className="text-sm font-semibold text-[#0A66F0] underline">
      Open {label}
    </a>
  );
}

/** "View Doc" — opens the private document in a dialog. */
export function PrivateDocumentLink({ url, label, className = "" }: { url: string; label: string; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={className || "text-xs font-medium text-[var(--color-primary)] underline"}>
        View Doc
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title={label} maxWidth="max-w-md">
        {open && <PrivateDocumentView url={url} label={label} />}
      </Modal>
    </>
  );
}
