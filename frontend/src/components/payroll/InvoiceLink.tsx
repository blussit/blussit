import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

/** Where an invoice document lives for the signed-in role. */
export function invoicePath(role: string | undefined, id: string): string {
  if (role === "admin") return `/admin/captain-salary/invoice/${id}`;
  if (role === "captain") return `/captain/earnings/invoice/${id}`;
  return `/manager/salary/invoice/${id}`;
}

/** An invoice number that opens the invoice. With `ids` each number links; without, it is plain text. */
export function InvoiceLink({ numbers, ids }: { numbers: string[]; ids?: string[] }) {
  const { user } = useAuth();
  return (
    <>
      {numbers.map((n, i) => (
        <span key={n}>
          {i > 0 && ", "}
          {ids?.[i] ? (
            <Link to={invoicePath(user?.role, ids[i])} className="font-mono-num underline underline-offset-2" onClick={(e) => e.stopPropagation()}>
              {n}
            </Link>
          ) : (
            <span className="font-mono-num">{n}</span>
          )}
        </span>
      ))}
    </>
  );
}
