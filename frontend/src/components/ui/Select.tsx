import {
  Children,
  forwardRef,
  isValidElement,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type OptionHTMLAttributes,
  type ReactElement,
  type SelectHTMLAttributes,
} from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "../../lib/cn";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
}

type OptionEl = ReactElement<OptionHTMLAttributes<HTMLOptionElement>>;

// Native <select> popups are rendered by the OS/webview shell outside our DOM and CSS —
// inside an Electron/VSCode webview that popup can land detached from the trigger,
// unstyled, and clipped off the visible panel. This is a fully in-DOM replacement (a
// button + an absolutely-positioned, theme-styled listbox) that keeps the exact same
// external API (value/onChange/children <option>) so every existing call site works
// unchanged, but the dropdown always renders anchored to its trigger, inside our own
// rounded/themed panel, and flips upward if it would otherwise run off the bottom.
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, label, error, id, children, value, defaultValue, onChange, disabled, name, required, ...rest }, ref) => {
    const selectId = id || label?.toLowerCase().replace(/\s+/g, "-");
    const [open, setOpen] = useState(false);
    const [openUpward, setOpenUpward] = useState(false);
    const wrapperRef = useRef<HTMLDivElement>(null);
    const hiddenSelectRef = useRef<HTMLSelectElement>(null);

    const options: OptionEl[] = Children.toArray(children).filter(
      (child): child is OptionEl => isValidElement(child) && child.type === "option"
    );

    const currentValue = value !== undefined ? value : defaultValue;
    const selected = options.find((o) => (o.props.value ?? String(o.props.children)) === currentValue) ?? options[0];

    useEffect(() => {
      if (!open) return;
      const onClick = (e: MouseEvent) => {
        if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
      };
      const onKey = (e: KeyboardEvent) => {
        if (e.key === "Escape") setOpen(false);
      };
      document.addEventListener("mousedown", onClick);
      document.addEventListener("keydown", onKey);
      return () => {
        document.removeEventListener("mousedown", onClick);
        document.removeEventListener("keydown", onKey);
      };
    }, [open]);

    useLayoutEffect(() => {
      if (!open || !wrapperRef.current) return;
      const rect = wrapperRef.current.getBoundingClientRect();
      const estimatedPanelHeight = Math.min(options.length * 40 + 16, 256);
      const spaceBelow = window.innerHeight - rect.bottom;
      setOpenUpward(spaceBelow < estimatedPanelHeight && rect.top > estimatedPanelHeight);
    }, [open, options.length]);

    const selectOption = (opt: OptionEl) => {
      const optValue = opt.props.value ?? String(opt.props.children);
      setOpen(false);
      if (opt.props.disabled) return;
      const nativeSelect = hiddenSelectRef.current;
      if (nativeSelect) {
        nativeSelect.value = String(optValue);
        onChange?.({ target: nativeSelect, currentTarget: nativeSelect } as unknown as React.ChangeEvent<HTMLSelectElement>);
      }
    };

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={selectId} className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
            {label}
          </label>
        )}
        <div className="relative" ref={wrapperRef}>
          {/* Kept in the DOM (visually hidden, not display:none) so refs/forms/required-validation and the
              onChange(event) contract all keep working exactly like a real <select>. */}
          <select
            ref={(node) => {
              hiddenSelectRef.current = node;
              if (typeof ref === "function") ref(node);
              else if (ref) ref.current = node;
            }}
            id={selectId}
            name={name}
            required={required}
            disabled={disabled}
            value={value}
            defaultValue={defaultValue}
            onChange={onChange}
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            {...rest}
          >
            {children}
          </select>

          <button
            type="button"
            disabled={disabled}
            onClick={() => setOpen((o) => !o)}
            aria-haspopup="listbox"
            aria-expanded={open}
            className={cn(
              "flex w-full items-center justify-between gap-2 rounded-xl border bg-[var(--color-surface,#fff)] px-3.5 py-2.5 text-left text-sm text-[var(--color-text-primary)] transition-colors",
              "focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]",
              disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:border-[var(--color-primary)]",
              error ? "border-[var(--color-error)]" : "border-gray-300",
              className
            )}
          >
            <span className="truncate">{selected ? selected.props.children : ""}</span>
            <ChevronDown className={cn("h-4 w-4 shrink-0 text-gray-400 transition-transform", open && "rotate-180")} />
          </button>

          {open && (
            <ul
              role="listbox"
              className={cn(
                "absolute z-50 max-h-64 w-full overflow-y-auto rounded-xl border border-gray-200 bg-[var(--color-surface,#fff)] p-1 shadow-lg",
                openUpward ? "bottom-full mb-1.5" : "top-full mt-1.5"
              )}
            >
              {options.map((opt, i) => {
                const optValue = opt.props.value ?? String(opt.props.children);
                const isSelected = selected && (selected.props.value ?? String(selected.props.children)) === optValue;
                return (
                  <li
                    key={String(optValue) + i}
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => selectOption(opt)}
                    className={cn(
                      "flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm transition-colors",
                      opt.props.disabled
                        ? "cursor-not-allowed text-gray-400"
                        : isSelected
                          ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium"
                          : "text-[var(--color-text-primary)] hover:bg-gray-100"
                    )}
                  >
                    <span className="truncate">{opt.props.children}</span>
                    {isSelected && <Check className="h-4 w-4 shrink-0" />}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        {error && <p className="mt-1 text-xs text-[var(--color-error)]">{error}</p>}
      </div>
    );
  }
);
Select.displayName = "Select";
