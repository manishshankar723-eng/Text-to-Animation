// The account dropdown — ONE menu, opened from two places.
//
// ⚠ IT IS A COMPONENT AND NOT TWO COPIES OF THE SAME MARKUP, and that is the
// whole point of the file. The editor's ⚙ in the top bar and the sidebar's
// account button are the two doors people reach for when they want "me" rather
// than "this project", and they were about to be two lists that agreed until
// somebody edited one of them. Adding a row here adds it to both.
//
// ⚠ AND IT REPLACED A MODAL. The sidebar button used to dim the app and put up
// a card asking "are you sure you want to log out?" — a dialog for a menu's
// worth of choices, exactly the mistake ＋ Add layer already made and undid.
// User-reported: "not need pop up here … like seeting dropdron same open in
// accouct buttun".
//
// The surface is `.tl-layer-menu`'s, deliberately: ＋ Add layer and a clip's
// right-click menu are already that panel, and a popover drawn its own way
// would read as another app's control. See the note beside it in
// animatic-editor.css.
import { useEffect, useState } from "react";
import Avatar from "./Avatar.jsx";

// Where "Help / Contact us" writes to. ⚠ A PLACEHOLDER — there is no support
// page and no support inbox yet; change it here and the row, its tooltip and
// the draft's subject all follow. The day there is a page, swap the row's
// `on` for a handler prop like the other three.
export const SUPPORT_EMAIL = "support@immersivedata.ai";

/**
 * Escape and an outside press close a dropdown, and both have to be written by
 * hand — the modal overlay they replaced did the two of them for free.
 *
 * ⚠ THE TRIGGER IS EXEMPT from the outside-press close, which is why `selector`
 * has to name it as well as the menu: closing on the button's `pointerdown`
 * would let the `click` that follows reopen what the press just shut, which
 * looks exactly like a dead button.
 *
 * @param {boolean} open
 * @param {() => void} close
 * @param {string} selector CSS selector matching the menu AND its trigger.
 */
export function useMenuDismiss(open, close, selector) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") close();
    };
    const onDown = (e) => {
      if (e.target.closest?.(selector)) return;
      close();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open, close, selector]);
}

/**
 * @param {object}   p
 * @param {Function} [p.onOpenAccount] Your account. Omitted → the row hides.
 * @param {Function} [p.onOpenPricing] Pricing and plan. Omitted → hides.
 * @param {Function} [p.onLogout]      Log out. Omitted → hides.
 * @param {Array}    [p.extra]  Rows inserted before Log out — `{ id, ico,
 *   label, note, on, danger }`, the same shape the built-in rows use. This is
 *   how the editor gets "🗑 Delete project", which is about the PROJECT and so
 *   has no business in the sidebar's copy of the menu.
 * @param {string}   [p.helpSubject] Subject line for the mail draft.
 * @param {boolean}  [p.help] Show "Help / Contact us". ⚠ IT NEEDS ITS OWN FLAG
 *   where the other three do not: they hide when the caller hands down no
 *   handler, but Help's handler is built in (a `mailto:`), so there is nothing
 *   to leave out. The editor's ⚙ turns it off — see AnimaticEditor.jsx.
 * @param {string}   [p.className]   Anchoring class — this component says what
 *   the menu IS, the caller's stylesheet says where it opens.
 * @param {Function} [p.onPick] Run after any row's own handler; a caller that
 *   keeps the open/closed flag itself uses this to close.
 * @param {string}   [p.label] What a screen reader calls the menu. The editor's
 *   copy holds no account rows at all, so "Account" would be a lie there.
 * @param {Array}    [p.accounts] `[{ email, name }]` - every account whose token
 *   is held. PASSING THIS TURNS "Your account" INTO A SUBMENU: instead of going
 *   straight to the profile it opens the switcher beside it, the way a browser
 *   profile picker does. Without it the row stays a plain link to `onOpenAccount`.
 * @param {string}   [p.activeEmail] Which of them is signed in - the tick.
 * @param {Function} [p.onSwitchAccount] `(email) => void`.
 * @param {Function} [p.onAddAccount] Opens the sign-in dialog.
 */
export default function AccountMenu({
  onOpenAccount,
  onOpenPricing,
  onLogout,
  extra = [],
  helpSubject = "Help",
  help = true,
  className = "",
  onPick,
  label = "Account",
  accounts,
  activeEmail,
  onSwitchAccount,
  onAddAccount,
}) {
  // Which row's submenu is open. Only "Your account" has one today, so this is
  // a boolean - a row id if a second one ever grows a flyout.
  const [sub, setSub] = useState(false);
  // ⚠ THE SWITCHER IS ONLY A SUBMENU IF THERE IS SOMETHING TO SWITCH BETWEEN
  // or somewhere to add one. With neither, "Your account" would open a panel
  // listing you, and you are already looking at yourself in the button below.
  const canSwitch = Boolean(accounts?.length && (onSwitchAccount || onAddAccount));
  // ⚠ ONE ARRAY, ONE ROW PER ENTRY. Another item in the menu is another object
  // here and nothing else — "if i need so i add more buttun name".
  const rows = [
    {
      id: "account",
      ico: "👤",
      label: "Your account",
      note: "Your profile, name and sign-in details",
      // Hidden rather than greyed when no handler was handed down: a row that
      // can never do anything is furniture, not a choice.
      on: onOpenAccount,
      // Opens the switcher beside the menu instead of navigating.
      sub: canSwitch,
    },
    {
      id: "pricing",
      ico: "⚡",
      label: "Pricing and plan",
      note: "What you're on, and what more costs",
      on: onOpenPricing,
    },
    {
      id: "help",
      ico: "❓",
      label: "Help / Contact us",
      note: `Write to ${SUPPORT_EMAIL}`,
      on:
        help &&
        (() => {
          window.location.href = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
            helpSubject
          )}`;
        }),
    },
    // Whatever this screen has that the other doesn't — the editor's Delete.
    // Above Log out, because logging out is the way OUT and belongs last.
    ...extra,
    {
      id: "logout",
      ico: "⎋",
      label: "Log out",
      note: "Sign out of your account",
      on: onLogout,
      danger: true,
    },
  ].filter((opt) => opt.on);

  return (
    <div className={`tl-layer-menu ${className}`} role="menu" aria-label={label}>
      {rows.map((opt) =>
        opt.sub ? (
          // ⚠ THE ROW THAT OPENS A SUBMENU DOES NOT CLOSE THE MENU. Every
          // other row here ends the interaction - this one continues it, so
          // `onPick` is deliberately not called.
          <span className="am-sub-wrap" key={opt.id}>
            <button
              type="button"
              role="menuitem"
              className={`tl-layer-menu-opt am-has-sub ${sub ? "on" : ""}`}
              title="Switch accounts, or add another"
              aria-haspopup="menu"
              aria-expanded={sub}
              onClick={() => setSub((open) => !open)}
            >
              <span className="tl-layer-menu-ico">{opt.ico}</span>
              {opt.label}
              {/* Points at where the panel comes out, which is the only thing
                  that tells you this row behaves differently from its
                  neighbours before you press it. */}
              <span className="am-sub-caret" aria-hidden="true">
                ›
              </span>
            </button>

            {sub && (
              <div className="tl-layer-menu am-switch" role="menu" aria-label="Switch accounts">
                <span className="am-switch-head">Switch accounts</span>

                {accounts.map((a) => {
                  const on = a.email === activeEmail;
                  return (
                    <button
                      key={a.email}
                      type="button"
                      role="menuitemradio"
                      aria-checked={on}
                      className={`tl-layer-menu-opt am-acct ${on ? "on" : ""}`}
                      /* The one you are already in is not a destination. */
                      disabled={on || !onSwitchAccount}
                      title={on ? `Signed in as ${a.email}` : `Switch to ${a.email}`}
                      onClick={() => {
                        onPick?.();
                        onSwitchAccount(a.email);
                      }}
                    >
                      <Avatar
                        size={28}
                        initial={(a.name || a.email || "?").trim().charAt(0).toUpperCase()}
                      />
                      <span className="am-acct-text">
                        {/* The address is the identity; a display name is a
                            nicety, so the address is always shown and the name
                            only when there is one. */}
                        <span className="am-acct-name">{a.name || a.email}</span>
                        {a.name && <span className="am-acct-mail">{a.email}</span>}
                      </span>
                      {on && (
                        <span className="am-acct-tick" aria-hidden="true">
                          ✓
                        </span>
                      )}
                    </button>
                  );
                })}

                <span className="am-switch-rule" />

                {onAddAccount && (
                  <button
                    type="button"
                    role="menuitem"
                    className="tl-layer-menu-opt"
                    title="Sign in to another account and keep this one"
                    onClick={() => {
                      onPick?.();
                      onAddAccount();
                    }}
                  >
                    <span className="tl-layer-menu-ico">＋</span>
                    Add another account
                  </button>
                )}
                {opt.on && (
                  <button
                    type="button"
                    role="menuitem"
                    className="tl-layer-menu-opt"
                    title="Your profile, and the accounts signed in on this browser"
                    onClick={() => {
                      onPick?.();
                      opt.on();
                    }}
                  >
                    <span className="tl-layer-menu-ico">⚙</span>
                    Manage accounts
                  </button>
                )}
              </div>
            )}
          </span>
        ) : (
          <button
            key={opt.id}
            type="button"
            role="menuitem"
            className={`tl-layer-menu-opt ${opt.danger ? "danger" : ""}`}
            title={opt.note}
            onClick={() => {
              onPick?.();
              opt.on();
            }}
          >
            <span className="tl-layer-menu-ico">{opt.ico}</span>
            {opt.label}
          </button>
        )
      )}
    </div>
  );
}
