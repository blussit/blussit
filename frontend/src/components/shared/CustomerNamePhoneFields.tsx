import { useEffect, useRef, useState, type Ref } from "react";
import { Input } from "../ui";
import { useCustomerTypeahead } from "../../hooks/useCustomerTypeahead";
import { cleanMobileInput } from "../../lib/validators";
import type { User } from "../../types";

interface CustomerNamePhoneFieldsProps {
  name: string;
  phone: string;
  onChangeName: (value: string) => void;
  onChangePhone: (value: string) => void;
  onPick?: (customer: User) => void;
  nameError?: string;
  phoneError?: string;
  phoneHint?: string;
  phoneInputRef?: Ref<HTMLInputElement>;
}

/**
 * Name + mobile inputs shared by every "book/sell on a customer's behalf"
 * form (manager New booking, Log a done job, Sell a plan) — with a
 * suggestions dropdown of existing customers matching what's typed, so a
 * manager picks the real account instead of accidentally creating a
 * duplicate one. Picking fills both fields and hands the full record back
 * via onPick (id included) for callers that want it, e.g. to show that
 * customer's active passes.
 */
export function CustomerNamePhoneFields({
  name,
  phone,
  onChangeName,
  onChangePhone,
  onPick,
  nameError,
  phoneError,
  phoneHint,
  phoneInputRef,
}: CustomerNamePhoneFieldsProps) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const query = picked ? "" : name.trim() || phone.trim();
  const suggestions = useCustomerTypeahead(query);

  useEffect(() => {
    function onOutsideClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onOutsideClick);
    return () => document.removeEventListener("mousedown", onOutsideClick);
  }, []);

  const pick = (customer: User) => {
    setPicked(true);
    setOpen(false);
    onChangeName(customer.full_name);
    onChangePhone(customer.phone || "");
    onPick?.(customer);
  };

  return (
    <div ref={containerRef} className="relative grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Input
        label="Customer name"
        maxLength={100}
        value={name}
        onChange={(e) => {
          setPicked(false);
          onChangeName(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        error={nameError}
        placeholder="E.g. Rahul Sharma"
        autoComplete="off"
      />
      <Input
        ref={phoneInputRef}
        label="Customer mobile"
        value={phone}
        inputMode="numeric"
        onChange={(e) => {
          setPicked(false);
          onChangePhone(cleanMobileInput(e.target.value));
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        error={phoneError}
        placeholder="10-digit mobile"
        hint={phoneHint}
        autoComplete="off"
      />
      {open && suggestions.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-xl border border-[#F3E5B5] bg-white shadow-lg">
          <p className="border-b border-[#F3E5B5] bg-[#FAFAFA] px-4 py-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-500">
            Existing customer{suggestions.length > 1 ? "s" : ""}
          </p>
          {suggestions.map((u) => (
            <button
              key={u.id}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(u)}
              className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm hover:bg-[#FFF4CD]"
            >
              <span className="font-medium text-black">{u.full_name}</span>
              <span className="text-gray-500">{u.phone}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
