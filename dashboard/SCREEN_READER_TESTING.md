# Screen Reader Testing Checklist

**Date:** January 19, 2026
**Tester:** _______________
**Sites:** Marketing website + Dashboard

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

- **Marketing Website:** http://localhost:3000/us (or https://aelira.ai)
- **Dashboard Login:** http://localhost:5173/login (or https://dashboard.aelira.ai/login)

---

## Flow 1: Marketing Website Homepage

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Page loads | Announces page title "Aelira" | [ ] | [ ] | [ ] |
| Skip link | "Skip to main content" link announced on Tab | [ ] | [ ] | [ ] |
| Logo link | "Aelira home, link" announced | [ ] | [ ] | [ ] |
| Navigation | Menu items announced with roles | [ ] | [ ] | [ ] |
| Dropdown menus | "Solutions, collapsed" / "expanded" announced | [ ] | [ ] | [ ] |
| Hero heading | Main h1 announced correctly | [ ] | [ ] | [ ] |
| CTA buttons | Button text and role announced | [ ] | [ ] | [ ] |
| Feature cards | Headings and content read in order | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 2: Dashboard Login Page

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

## Flow 3: Dashboard Main (after login)

| Test | Expected Behavior | macOS | Win | Ubuntu |
|------|-------------------|-------|-----|--------|
| Skip link | "Skip to main content" link on Tab | [ ] | [ ] | [ ] |
| Navigation | Sidebar navigation announced | [ ] | [ ] | [ ] |
| Page heading | "Dashboard" h1 announced | [ ] | [ ] | [ ] |
| Stats cards | Statistics read with context | [ ] | [ ] | [ ] |
| Loading spinner | "Loading dashboard" announced | [ ] | [ ] | [ ] |
| Error state | Error alert announced if shown | [ ] | [ ] | [ ] |
| Empty state | Empty state message read | [ ] | [ ] | [ ] |

**Notes:**
```


```

---

## Flow 4: Upload Page

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

## Flow 5: Scan Results / History

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

## Flow 6: Settings Page

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
| macOS (VoiceOver) | /42 | /42 | |
| Windows (NVDA/Narrator) | /42 | /42 | |
| Ubuntu (Orca) | /42 | /42 | |

**Overall Status:** [ ] PASS / [ ] FAIL

**Tester Signature:** _______________
**Date Completed:** _______________
