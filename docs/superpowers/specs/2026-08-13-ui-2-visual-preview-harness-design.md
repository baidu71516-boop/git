# UI-2 Visual Preview Harness Design

## Goal

Provide an opt-in local visual preview of the redesigned influencer table without changing the real `/influencers` route, calling Backend/Auth, or writing persistent data.

## Route and production isolation

- Add `/dev-ui-preview/influencers` as a separate Next.js App Router page. The explicit `dev-` prefix avoids App Router's private-folder handling for underscore-prefixed directories.
- The server page checks `process.env.NODE_ENV` before rendering.
- Outside development it calls `notFound()`, so fixtures cannot render in production.
- The route is not linked from the App Shell or any product navigation.

## Rendering architecture

- Render the existing `AppShell`, `InfluencerFilterBar`, `InfluencerTable`, and `InfluencerPagination` components.
- Reuse the existing table formatters, badges, tooltips, and UI-2 CSS through those components.
- Keep preview controls static: callbacks are no-ops and no query/search/pagination behavior is reimplemented.
- Use a subtle `仅用于界面预览` label near the page description.

## Data

- Store eight obviously fictional `InfluencerListItem` records in a Web-only fixture module.
- Cover zero, null, sub-10k, 100k+, and million-scale followers; zero through four-plus tags; unset and real CRM stages; contact-type combinations; assigned and unassigned owners; and current through null metric timestamps.
- Contact values use display-only placeholders and are never sent anywhere.
- Relative timestamps are generated in memory when the preview renders so today/yesterday/three-days-ago remain visually meaningful.

## Safety boundaries

- Do not import the API client, AuthShell, React Query hooks, Backend packages, or database code.
- Do not monkey-patch `fetch` or override `/api/v1/influencers`.
- Do not modify API contracts, migrations, schema, or persistent data.
- Keep `/influencers` unchanged and covered by its existing API-request tests.

## Verification

- Test the fixture has eight representative records and uses the existing response item type.
- Test the preview workspace renders all eight rows and the expected formatter output without issuing `fetch`.
- Test route gating calls `notFound()` outside development.
- Run Prettier, ESLint, TypeScript, all Web tests, and `git diff --check`.
- Visually verify the development route in a browser.
