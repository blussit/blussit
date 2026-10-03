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
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "../../lib/cn";
import {
  FIELD_ERROR,
  FIELD_ICON,
  FIELD_LABEL,
  FIELD_PLACEHOLDER,
  MENU_ITEM,
  MENU_ITEM_DISABLED,
  MENU_ITEM_IDLE,
  MENU_ITEM_SELECTED,
  MENU_PANEL,
  fieldBox,
} from "./fieldStyles";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  /** 40px tall instead of 44px — for filter rows, table cells and headers. */
  compact?: boolean;
  /** Classes for the outer wrapper (replaces the default `w-full`), e.g. to
   *  let a compact Select sit inline in a flex row. */
  wrapperClassName?: string;
}

type OptionEl = ReactElement<OptionHTMLAttributes<HTMLOptionElement>>;

// Native <select> popups are rendered by the OS/webview shell outside our DOM and CSS —
// inside an Electron/VSCode webview that popup can land detached from the trigger,
// unstyled, and clipped off the visible panel. This is a fully in-DOM replacement (a
// button + a portalled, fixed-position listbox) that keeps the exact same external API
// (value/onChange/children <option>) so every existing call site works unchanged.
// The listbox is rendered into document.body via a portal — NOT inside the trigger's
// wrapper — because any ancestor with overflow (a Modal's overflow-y-auto, a Card, a
// DataTable's overflow-x-auto) would otherwise clip the open panel, which is exactly
// the "dropdowns are cut off" bug this replaces. It positions itself off the trigger's
// rect, flips upward when there's no room below, and follows scroll/resize.
// Look: the shared v2 field (ui/fieldStyles) — same box as Input/DatePicker/TimePicker.
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    { className, label, error, id, children, value, defaultValue, onChange, disabled, name, required, compact = false, wrapperClassName, ...rest },
    ref
  ) => {
    const selectId = id || label?.toLowerCase().replace(/\s+/g, "-");
    const labelId = label && selectId ? `${selectId}-label` : undefined;
    const [open, setOpen] = useState(false);
    const [panelStyle, setPanelStyle] = useState<React.CSSProperties | null>(null);
    const wrapperRef = useRef<HTMLDivElement>(null);
    const panelRef = useRef<HTMLUListElement>(null);
    const hiddenSelectRef = useRef<HTMLSelectElement>(null);

    const options: OptionEl[] = Children.toArray(children).filter(
      (child): child is OptionEl => isValidElement(child) && child.type === "option"
    );

    const currentValue = value !== undefined ? value : defaultValue;
    const selected = options.find((o) => (o.props.value ?? String(o.props.children)) === currentValue) ?? options[0];
    const selectedValue = selected ? (selected.props.value ?? String(selected.props.children)) : undefined;
    // An empty-value option ("Select a category", "All statuses") reads as the placeholder.
    const showingPlaceholder = selectedValue === "";

    useEffect(() => {
      if (!open) return;
      const onClick = (e: MouseEvent) => {
        const t = e.target as Node;
        if (wrapperRef.current?.contains(t) || panelRef.current?.contains(t)) return;
        setOpen(false);
      };
      const onKey = (e: KeyboardEvent) => {
        if (e.key !== "Escape") return;
        // Esc closes just this list — don't let it also reach a window-level
        // "Esc closes the dialog" handler and throw away a half-filled form.
        e.stopPropagation();
        setOpen(false);
      };
      document.addEventListener("mousedown", onClick);
      document.addEventListener("keydown", onKey);
      return () => {
        document.removeEventListener("mousedown", onClick);
        document.removeEventListener("keydown", onKey);
      };
    }, [open]);

    // Fixed positioning computed from the trigger's viewport rect; kept in
    // sync while scrolling any ancestor (capture phase catches them all)
    // and on resize, so the panel stays glued to its trigger. A narrow
    // compact trigger still gets a readable panel, clamped on-screen.
    useLayoutEffect(() => {
      if (!open) return;
      const place = () => {
        const rect = wrapperRef.current?.getBoundingClientRect();
        if (!rect) return;
        const margin = 8;
        const panelHeight = Math.min(options.length * 40 + 16, 256);
        const spaceBelow = window.innerHeight - rect.bottom;
        const upward = spaceBelow < panelHeight && rect.top > panelHeight;
        const width = Math.min(Math.max(rect.width, compact ? 184 : 0), window.innerWidth - margin * 2);
        const left = Math.min(Math.max(rect.left, margin), Math.max(margin, window.innerWidth - width - margin));
        setPanelStyle({
          position: "fixed",
          left,
          width,
          maxHeight: 256,
          ...(upward ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
        });
      };
      place();
      window.addEventListener("scroll", place, true);
      window.addEventListener("resize", place);
      return () => {
        window.removeEventListener("scroll", place, true);
        window.removeEventListener("resize", place);
      };
    }, [open, options.length, compact]);

    // Long lists open scrolled to the current choice.
    useEffect(() => {
      if (!open || !panelStyle) return;
      panelRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    }, [open, panelStyle]);

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
      <div className={wrapperClassName ?? "w-full"}>
        {label && (
          <label id={labelId} htmlFor={selectId} className={FIELD_LABEL}>
            {label}
          </label>
        )}
        <div className="relative" ref={wrapperRef}>
          <button
            type="button"
            disabled={disabled}
            onClick={() => setOpen((o) => !o)}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-labelledby={labelId}
            aria-label={labelId ? undefined : rest["aria-label"]}
            aria-invalid={error ? true : undefined}
            title={rest.title}
            className={cn(
              fieldBox({ error, open, disabled, compact }),
              "flex items-center justify-between gap-2 pr-3 text-left",
              compact && "pl-3",
              !disabled && "cursor-pointer",
              className
            )}
          >
            <span className={cn("min-w-0 truncate", showingPlaceholder && !disabled && FIELD_PLACEHOLDER)}>
              {selected ? selected.props.children : ""}
            </span>
            <ChevronDown className={cn(FIELD_ICON, "transition-transform duration-150", open && "rotate-180 text-[#0A66F0]")} />
          </button>

          {/* Kept in the DOM (visually hidden, not display:none) so refs/forms/required-validation and the
              onChange(event) contract all keep working exactly like a real <select>. It sits after the
              button so a wrapping <label> activates the button, not this. */}
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

          {open &&
            panelStyle &&
            createPortal(
              <ul
                ref={panelRef}
                role="listbox"
                aria-labelledby={labelId}
                style={panelStyle}
                className={cn("z-[100010] overflow-y-auto overscroll-contain p-1", MENU_PANEL)}
              >
                {options.map((opt, i) => {
                  const optValue = opt.props.value ?? String(opt.props.children);
                  const isSelected = selectedValue === optValue;
                  return (
                    <li
                      key={String(optValue) + i}
                      role="option"
                      aria-selected={isSelected}
                      aria-disabled={opt.props.disabled || undefined}
                      onClick={() => selectOption(opt)}
                      className={cn(
                        MENU_ITEM,
                        "flex items-center justify-between gap-2",
                        opt.props.disabled
                          ? MENU_ITEM_DISABLED
                          : cn("cursor-pointer", isSelected ? MENU_ITEM_SELECTED : MENU_ITEM_IDLE)
                      )}
                    >
                      <span className="truncate">{opt.props.children}</span>
                      {isSelected && <Check className="h-4 w-4 shrink-0 text-[#0A66F0]" />}
                    </li>
                  );
                })}
              </ul>,
              document.body
            )}
        </div>
        {error && <p className={FIELD_ERROR}>{error}</p>}
      </div>
    );
  }
);
Select.displayName = "Select";
