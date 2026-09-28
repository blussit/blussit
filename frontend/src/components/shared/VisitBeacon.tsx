import { useEffect } from "react";
import { useAuth } from "../../context/AuthContext";
import { apiClient } from "../../lib/api-client";
import { todayIST } from "../../lib/date";

const DEVICE_KEY = "blussit:device_id";
const SENT_KEY = "blussit:visit_sent";
const STAFF_ROLES = new Set(["admin", "manager", "captain"]);

function readOrCreateDeviceId(): string | null {
  try {
    const existing = localStorage.getItem(DEVICE_KEY);
    if (existing) return existing;
    const id = crypto.randomUUID();
    localStorage.setItem(DEVICE_KEY, id);
    return id;
  } catch {
    return null;
  }
}

/** Counts one website visit per device per IST day. The server dedups on
 *  (device, day) — the local "already sent today" mark only saves a
 *  pointless request on every page load. Staff are never counted. */
export function VisitBeacon() {
  const { user, isLoading } = useAuth();

  useEffect(() => {
    if (isLoading || (user && STAFF_ROLES.has(user.role))) return;
    const today = todayIST();
    try {
      if (localStorage.getItem(SENT_KEY) === today) return;
    } catch {
      // storage blocked — still count, the server dedups
    }
    const deviceId = readOrCreateDeviceId();
    if (!deviceId) return;
    apiClient
      .post("/analytics/visit", { device_id: deviceId })
      .then(() => {
        try {
          localStorage.setItem(SENT_KEY, today);
        } catch {
          // ignore
        }
      })
      .catch(() => undefined);
  }, [isLoading, user]);

  return null;
}
