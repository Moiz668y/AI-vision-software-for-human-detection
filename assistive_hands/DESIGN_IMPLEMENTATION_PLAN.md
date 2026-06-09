# AssistiveHands Design Implementation Plan

## Goal

Turn AssistiveHands into a consistent, accessible, gaze-friendly operational dashboard where users can always navigate back, controls are stable, visual hierarchy is clear, and the UI does not fight the eye/mouse tracking system.

## Phase 1: App Shell And Navigation Foundation

### Objective

Make every page feel like part of the same app and guarantee the user can always return.

### Files

- `assistive_hands/ui/templates/dashboard.html`
- `assistive_hands/ui/templates/communication.html`
- `assistive_hands/ui/templates/calibration.html`
- `assistive_hands/ui/templates/settings.html`
- `assistive_hands/ui/templates/setup.html`
- `assistive_hands/ui/templates/debug.html`
- possibly a new shared template or partial

### Tasks

1. Create a shared app header pattern.
   Include:
   - AssistiveHands brand
   - Dashboard/Home link
   - Back button
   - Calibration
   - Communication
   - Settings
   - optional More menu for Setup/Debug

2. Add a persistent "Back to Dashboard" control on secondary pages.
   This should be large enough for gaze selection and placed consistently near the top-left or top-right.

3. Add active page state.
   The current page should be visually highlighted in the nav.

4. Make navigation responsive.
   Desktop should use horizontal nav. Mobile/narrow views should use a collapsible nav with large menu items.

5. Add accessibility labels.
   Navbar toggler needs `aria-label`, `aria-controls`, and `aria-expanded`. Back/Home buttons need clear text, not icon-only affordances.

### Acceptance Criteria

- From any page, the user can return to Dashboard without browser Back.
- Header looks consistent across Dashboard, Calibration, Communication, Settings, Setup, and Debug.
- Gaze users have large, predictable nav targets.

## Phase 2: Dashboard Information Architecture

### Objective

Make the dashboard useful as an assistive control surface, not a noisy demo page.

### Files

- `assistive_hands/ui/templates/dashboard.html`
- `assistive_hands/ui/static/js/dashboard.js`
- `assistive_hands/ui/static/css/style.css`

### Tasks

1. Reorder the dashboard layout.
   Recommended order:
   - slim system status bar
   - main camera/control area
   - primary action controls
   - compact telemetry metrics
   - secondary help/status panels

2. Reduce metric card dominance.
   The current large gradient cards take too much first-screen space. Convert them into compact telemetry tiles or a horizontal status strip.

3. Move camera feed higher.
   The camera feed is the primary operational view. It should be visible without scrolling on normal desktop height.

4. Simplify the right sidebar.
   Group controls into:
   - Primary Actions
   - Cursor/Gaze Control
   - System Status
   - Shortcuts/Help

5. Make the shortcut panel readable.
   Replace the tiny dark list with a structured table:
   - Key
   - Action
   - Context

6. Remove duplicate or misleading controls.
   If Voice Commands are not implemented, mark them as disabled or move them to a "Coming soon" area.

### Acceptance Criteria

- The camera/control area is the visual anchor.
- Metrics are readable but no longer overpower the page.
- Shortcut instructions are understandable at a glance.
- User can see current gaze/camera state quickly.

## Phase 3: Visual System Cleanup

### Objective

Replace mixed Bootstrap/default/custom styling with one coherent design system.

### Files

- `assistive_hands/ui/static/css/style.css`
- all templates with inline styles

### Tasks

1. Remove duplicated CSS token systems.
   `style.css` currently defines `:root` twice with conflicting values. Consolidate to one token set.

2. Remove global font-size override.
   Replace `* { font-size: var(--text-size); }` with:
   - body base size
   - heading sizes
   - button sizes
   - compact text sizes
   - telemetry sizes

3. Stabilize hover/focus behavior.
   Remove card lift and button scale effects. Gaze targets should not move when hovered.
   Replace movement with:
   - border color change
   - background tint
   - focus ring
   - subtle shadow only if it does not shift layout

4. Standardize card design.
   Use one card style:
   - consistent border radius
   - consistent padding
   - consistent header treatment
   - no random `bg-dark`, `bg-secondary`, or `bg-info` unless representing state

5. Standardize color usage.
   Reserve color for meaning:
   - green: active/connected/success
   - yellow: warning/standby
   - red: disabled/error
   - blue: primary action/navigation
   - gray: neutral panels

6. Fix contrast.
   Avoid `text-muted` on dark or gradient backgrounds unless overridden with accessible contrast.

7. Move inline styles into CSS classes.
   Especially camera containers, calibration stage, status overlays, and shortcut panels.

### Acceptance Criteria

- UI no longer looks like several different design systems glued together.
- Hover/focus states do not move gaze targets.
- Text contrast is readable on all surfaces.
- CSS has one clear token system.

## Phase 4: Gaze-Friendly Interaction Design

### Objective

Make the UI usable by someone relying on gaze/dwell, not just mouse/keyboard.

### Files

- `assistive_hands/ui/templates/communication.html`
- `assistive_hands/ui/static/js/communication.js`
- `assistive_hands/ui/templates/dashboard.html`
- `assistive_hands/ui/static/js/dashboard.js`
- `assistive_hands/ui/static/css/style.css`

### Tasks

1. Define gaze target size rules.
   Recommended minimums:
   - primary buttons: 56-64px height
   - nav targets: 48-56px height
   - dense controls: no smaller than 44px

2. Enable dwell typing in Communication.
   Current code visually hovers keyboard keys but blocks activation. Design should support:
   - dwell to select key
   - visible dwell progress
   - cancel/reset when gaze leaves
   - optional dwell toggle

3. Register quick phrases as gaze targets.
   Quick phrases should be selectable by dwell just like keyboard keys.

4. Add gaze-safe escape controls.
   Communication and Calibration should always have:
   - Dashboard
   - Back
   - Pause gaze input
   - Cancel/Exit where relevant

5. Avoid moving layout during use.
   No hover scaling, no changing button sizes, no shifting cards.

6. Add clear paused/disabled states.
   If gaze control is disabled, show it prominently and provide a big Enable button.

### Acceptance Criteria

- A gaze-only user can navigate, type, and return home.
- Interactive targets are large and stable.
- Dwell progress is visible and predictable.
- Emergency/escape controls are always reachable.

## Phase 5: Calibration Page Redesign

### Objective

Make calibration feel guided, safe, and easy to exit.

### Files

- `assistive_hands/ui/templates/calibration.html`
- `assistive_hands/ui/static/js/calibration.js`
- `assistive_hands/ui/static/css/style.css`

### Tasks

1. Use shared header.
   Add Dashboard/Back controls.

2. Redesign calibration layout.
   Suggested layout:
   - large calibration stage
   - right-side progress and controls
   - face/eye quality status
   - clear Start/Pause/Cancel buttons

3. Improve status messaging.
   Current messages are scattered between canvas overlays, toasts, and side cards. Use one primary calibration status area.

4. Fix pause/resume design.
   The UI should show:
   - Running
   - Paused
   - Waiting for face
   - Complete
   - Failed/cancelled

5. Add clear cancel behavior.
   Cancel should visibly return to Dashboard or previous page after clearing calibration state.

6. Fix calibration target scaling design.
   Points should be based on actual canvas/screen dimensions, not hard-coded 1920x1080 assumptions.

### Acceptance Criteria

- User can always exit calibration.
- Calibration state is visually obvious.
- Pause/resume behavior is understandable.
- Targets appear where the backend expects them.

## Phase 6: Settings And Setup UX Cleanup

### Objective

Make settings/setup useful instead of half-wired screens.

### Files

- `assistive_hands/ui/templates/settings.html`
- `assistive_hands/ui/static/js/settings.js`
- `assistive_hands/ui/templates/setup.html`
- `assistive_hands/ui/static/js/setup.js`
- `assistive_hands/app.py` later when implementing behavior

### Tasks

1. Fix settings navigation design.
   Use a clearer sidebar or tab layout. Ensure active section state works when clicking text or icons.

2. Hide or label unimplemented settings.
   Do not show controls that do not actually affect the system unless marked "Not connected yet".

3. Make Save/Reset sticky or consistently placed.
   Settings pages should not require hunting for Save.

4. Fix setup step count.
   Either design all 5 setup steps or reduce the wizard to 2 real steps.

5. Add Dashboard/Skip Setup.
   Setup should not trap the user.

6. Remove camera-stop-on-leave behavior from the UX model.
   Page navigation should not unexpectedly disable the camera.

### Acceptance Criteria

- Settings sections switch reliably.
- Setup never shows blank steps.
- User can skip/exit setup.
- Settings communicate what is live vs placeholder.

## Phase 7: Runtime Feedback And Notifications

### Objective

Make status messages helpful without blocking or overlapping important controls.

### Files

- `assistive_hands/ui/static/js/utils.js`
- `assistive_hands/ui/static/js/android_camera.js`
- `assistive_hands/ui/static/css/style.css`
- relevant templates

### Tasks

1. Create one toast stack.
   Place below navbar, fixed or sticky, with max width.

2. Avoid overlapping right-side content.
   Current floating notifications/widgets can cover panels.

3. Replace browser `alert()` in communication.
   Use app toasts/status messages instead.

4. Add consistent status components.
   Use the same visual pattern for:
   - connected
   - inactive
   - calibrated
   - paused
   - error

5. Add persistent system status summary.
   A small status strip can show camera, face, gaze, cursor, and calibration.

### Acceptance Criteria

- Notifications do not cover primary controls.
- Alerts feel native to the app.
- System status is always understandable.

## Phase 8: Responsive And Accessibility Pass

### Objective

Make the app usable across screen sizes and input methods.

### Files

- `assistive_hands/ui/static/css/style.css`
- all templates

### Tasks

1. Add dashboard-specific breakpoints.
   Desktop:
   - camera left
   - controls right
   - compact metrics

   Tablet:
   - controls above secondary panels

   Mobile:
   - camera first
   - primary actions second
   - metrics/status later

2. Add focus styles for all interactive elements.
   Include:
   - `.nav-link`
   - `.navbar-brand`
   - `.list-group-item`
   - `.phrase-btn`
   - `.form-control`
   - `.form-select`

3. Guard keyboard shortcuts.
   Shortcuts should not fire when typing in input fields or text areas.

4. Use semantic labels.
   Buttons should have clear text or `aria-label`.

5. Respect reduced motion.
   Already partially present, but remove unnecessary transforms by default.

### Acceptance Criteria

- UI does not overlap or clip on common viewport sizes.
- Keyboard users can see focus location.
- Screen reader labels are reasonable.
- Shortcuts do not break normal typing.

## Phase 9: Implementation Validation Plan

### Navigation

- Dashboard to Communication and back.
- Dashboard to Calibration and back/cancel.
- Dashboard to Settings and back.
- Setup/Debug have escape routes.

### Gaze Usability

- Communication keyboard dwell works.
- Quick phrases dwell works.
- Pause/enable states are visible.
- Targets do not move on hover.

### Visual Checks

- No stray `@"` or `Out-File` text.
- No overlapping floating widgets.
- Consistent cards/nav/buttons.
- Readable contrast.

### Responsive Checks

- 1920x1080.
- 1366x768.
- Tablet width.
- Mobile width.

### Behavior Checks

- Camera does not stop when changing pages.
- Pause stops cursor/click side effects.
- Calibration cancel clears backend state.
- Settings save routes work.

## Suggested Work Order

1. Dashboard template corruption.
2. Shared header/back navigation.
3. CSS token cleanup.
4. Dashboard layout redesign.
5. Communication gaze/dwell usability.
6. Calibration page redesign.
7. Settings/setup cleanup.
8. Notification/status system.
9. Responsive/accessibility pass.
10. Final verification.
