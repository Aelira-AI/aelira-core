# Screen Reader Testing Checklist

This is a blank template for manually testing the dashboard with a screen reader — not a record of a completed test pass. Fill in the checkboxes, notes, and summary below as you run through it; an unfilled template with every box left `[ ]` means no test run has been recorded here yet.

**Site:** Dashboard (this repository — `dashboard/`)
**Tester:** _______________

---

## Quick Start Commands

### macOS - VoiceOver
- **Enable:** `Cmd + F5`
- **Navigation:** `Ctrl + Option + Arrow keys`
- **Interact:** `Ctrl + Option + Space`
- **Rotor (headings/links):** `Ctrl + Option + U`

### Windows 11 - NVDA
- **Download:** https://www.nvaccess.org/download/
- **Enable:** `Ctrl + Alt + N`
- **Navigation:** Arrow keys
- **Interact:** `Enter` or `Space`
- **Elements list:** `NVDA + F7`

### Windows 11 - Narrator
- **Enable:** `Win + Ctrl + Enter`
- **Navigation:** `Caps Lock + Arrow keys`
- **Scan mode:** `Caps Lock + Space`

### Ubuntu - Orca
- **Enable:** `Super + Alt + S`
- **Navigation:** Arrow keys
- **Interact:** `Enter` or `Space`

---

## Test URLs

- **Dashboard Login:** http://localhost:5173/login (or your deployed dashboard's `/login`)

---

## Flow 1: Dashboard Login Page

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Page loads | "Welcome Back" heading announced | [ ] | [ ] | [ ] |
| Main landmark | Page has main landmark | [ ] | [ ] | [ ] |
| Email input | "Email Address, edit text" announced | [ ] | [ ] | [ ] |
| Submit button | "Send Magic Link, button" announced | [ ] | [ ] | [ ] |
| Loading state | "Loading" announced when submitting | [ ] | [ ] | [ ] |
| Success message | Success alert announced automatically | [ ] | [ ] | [ ] |
| Error message | Error alert announced automatically | [ ] | [ ] | [ ] |
| OAuth buttons | "Google, button" / "Microsoft, button" | [ ] | [ ] | [ ] |
| API Key toggle | "Use API Key instead, collapsed/expanded" | [ ] | [ ] | [ ] |
| Theme toggle | "Switch to dark/light mode, button" | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 2: Dashboard Main (after login)

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Skip link | "Skip to main content" link on Tab | [ ] | [ ] | [ ] |
| Navigation | Sidebar navigation announced | [ ] | [ ] | [ ] |
| Page heading | "Dashboard" h1 announced | [ ] | [ ] | [ ] |
| Stats cards | Statistics read with context | [ ] | [ ] | [ ] |
| Loading spinner | "Loading dashboard" announced | [ ] | [ ] | [ ] |
| Error state | Error alert announced if shown | [ ] | [ ] | [ ] |
| Empty state | Empty state message read | [ ] | [ ] | [ ] |
| Issues-by-type chart | Chart announced via role="img" with a summarizing aria-label; bar/pie toggle group announced as "Chart type", buttons announce pressed/not pressed state | [ ] | [ ] | [ ] |
| WCAG criteria chart | Chart announced via role="img" with a summarizing aria-label | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 3: Upload Page

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Page heading | "Upload" h1 announced | [ ] | [ ] | [ ] |
| Drop zone | "Drag and drop files here" announced | [ ] | [ ] | [ ] |
| File input | Accessible file picker | [ ] | [ ] | [ ] |
| Upload button | "Upload, button" announced | [ ] | [ ] | [ ] |
| Progress | Upload progress announced | [ ] | [ ] | [ ] |
| File list | Uploaded files listed with names | [ ] | [ ] | [ ] |
| Remove button | "Remove file, button" with file name | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 4: Scan Results / History

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Page heading | "Scan History" h1 announced | [ ] | [ ] | [ ] |
| Loading state | "Loading history" announced | [ ] | [ ] | [ ] |
| Results table | Table navigation works | [ ] | [ ] | [ ] |
| Issue counts | Issue numbers announced | [ ] | [ ] | [ ] |
| Action buttons | "View details, button" announced | [ ] | [ ] | [ ] |
| Delete modal | Dialog announced with title | [ ] | [ ] | [ ] |
| Modal buttons | Confirm/Cancel announced | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 5: Settings Page

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Page heading | "Settings" h1 announced | [ ] | [ ] | [ ] |
| Section headings | h2 headings for each section | [ ] | [ ] | [ ] |
| Form labels | All inputs have labels announced | [ ] | [ ] | [ ] |
| Toggle switches | State announced (on/off) | [ ] | [ ] | [ ] |
| Save button | "Save, button" announced | [ ] | [ ] | [ ] |
| Success toast | "Success" toast announced | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 6: Cookie Consent Banner

`CookieBanner` shows on first visit (or after clearing `localStorage`'s `aelira-cookie-consent` key).

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Banner appears | Announced as a dialog with title "Cookie Notice" | [ ] | [ ] | [ ] |
| Accept/Reject/Preferences buttons | Each button's purpose announced | [ ] | [ ] | [ ] |
| Preferences modal opens | Announced as a dialog with title "Cookie Preferences" | [ ] | [ ] | [ ] |
| Preferences modal backdrop | Not announced/focusable (it's `role="presentation" aria-hidden`, decorative only — the explicit "Close preferences" button is the keyboard/screen-reader path out) | [ ] | [ ] | [ ] |
| Checkboxes | Functional/Analytics checkboxes announce label and checked state | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 7: Document Review — Focus Trap

`TableStructureEditor`'s scope selector (`src/components/review/TableStructureEditor.tsx`) uses the `useFocusTrap` hook (`src/hooks/useFocusTrap.ts`).

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Trap activates | Focus moves to the first focusable element inside the scope selector when it opens | [ ] | [ ] | [ ] |
| Tab wraps forward | Tabbing past the last focusable element wraps to the first | [ ] | [ ] | [ ] |
| Shift+Tab wraps backward | Shift+Tab from the first focusable element wraps to the last | [ ] | [ ] | [ ] |
| Focus restored on close | Closing returns focus to the element that opened the selector | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## General Accessibility Checks

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Focus visible | Focus indicator visible on all elements | [ ] | [ ] | [ ] |
| Focus order | Logical tab order (top to bottom, left to right) | [ ] | [ ] | [ ] |
| Escape key | Closes modals/dropdowns | [ ] | [ ] | [ ] |
| No focus traps | Can always tab out of components | [ ] | [ ] | [ ] |
| Live regions | Dynamic content announced | [ ] | [ ] | [ ] |
| Headings list | Can navigate by headings | [ ] | [ ] | [ ] |
| Links list | Can navigate by links | [ ] | [ ] | [ ] |
| Landmarks | Can navigate by landmarks | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Issues Found

### Critical (Blocks users)
| Issue | Location | Platform | Fix Required |
|-------|----------|----------|--------------|
| | | | |

### Serious (Major usability problem)
| Issue | Location | Platform | Fix Required |
|-------|----------|----------|--------------|
| | | | |

### Minor (Annoying but usable)
| Issue | Location | Platform | Fix Required |
|-------|----------|----------|--------------|
| | | | |

---

## Summary

| Platform | Tests Passed | Tests Failed | Notes |
|----------|--------------|--------------|-------|
| macOS (VoiceOver) | /56 | /56 | |
| Windows (NVDA/Narrator) | /56 | /56 | |
| Ubuntu (Orca) | /56 | /56 | |

**Overall Status:** [ ] PASS / [ ] FAIL

**Tester Signature:** _______________
**Date Completed:** _______________
