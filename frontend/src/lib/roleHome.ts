import type { UserRole } from "../types";

export function roleHomePath(role?: UserRole | null): string {
  switch (role) {
    case "admin":
      return "/admin";
    case "manager":
      return "/manager";
    case "captain":
      return "/captain";
    case "customer":
    default:
      return "/app";
  }
}
