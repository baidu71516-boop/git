# UI-1 Design System + App Shell

## Scope

UI-1 only changes the Web presentation shell. It does not change backend code,
API contracts, database schema, migrations, search semantics, or influencer/import
business components. Existing `/` and `/influencers` URLs retain their meaning.

## Architecture

`AuthShell` remains responsible for department-password login, session restore,
operator selection, role checks, CSRF-backed logout, and auth error handling.
After authentication it renders `AppShell`, which owns the visual frame:

- fixed 232px desktop sidebar;
- compact topbar with page title and identity menu trigger;
- content area with the existing ImportWorkspace or InfluencerWorkspace;
- sidebar footer with department, selected operator, role, and logout;
- mobile navigation drawer opened from a menu button.

Authentication is not duplicated in AppShell. Influencer routes do not require an
operator; the import workspace continues to require one and keeps the existing
selection modal behavior.

## Navigation

Only real or clearly unavailable destinations are shown:

- 达人库 -> `/influencers`;
- 数据采集 -> `/`;
- 工作台 and 导入记录 are omitted until a real route exists.

The active item is derived from the current pathname. No Campaign, Inbox,
Sequence, email, AI, or analytics placeholder navigation is added.

## Design Tokens

Ant Design `ConfigProvider` theme tokens and `globals.css` define a small shared
set: blue brand primary, pale blue-gray page background, white surfaces, light
gray borders, primary/secondary text, success/warning/danger states, 10px card
radius, 8px control radius, compact control height, spacing rhythm, and a light
shadow. The implementation avoids large gradients and marketing-style hero UI.

## Shared UI Primitives

The first version adds only shell-level wrappers needed by current and future
screens: `AppShell`, `SidebarNav`, `PageHeader`, `StatusBadge`, `AppTooltip`,
`AppEmpty`, `AppLoading`, and `UserMenu`. These wrappers compose Ant Design and
do not replace Ant Design Button/Input/Table components.

## Responsive Behavior

Desktop is the primary layout. At small widths the sidebar becomes an off-canvas
navigation drawer while the content remains usable with reduced padding. The
existing business layouts are not redesigned in UI-1.

## Verification

Regression coverage must preserve unauthenticated login, authenticated identity
rendering, Viewer access to influencers without an operator, mandatory operator
selection for imports, navigation links and active state, logout, and existing
`/` and `/influencers` loading behavior. Typecheck, lint, formatting, and Web
tests are required before the implementation commit.
