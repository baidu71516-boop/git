# Permissions V1 Task 5 minimal Web design

## Scope

Add the smallest auth response contract needed for a truthful Web shell, then
provide a functional same-department operator administration page at
`/admin/permissions`. No migration, authorization-policy change, visual
redesign, custom roles, or ACL work is included.

## Auth truth

`/auth/login`, `/auth/me`, and `/auth/select-operator` return a Department
ceiling field and a nullable effective role. The backend derives both from the
already-authoritative `AuthContext`; the Web never derives business authority
from legacy `role`, Department ceiling, or the operator directory.

## Web behavior

The authenticated shell stores the response contract. It displays `待选择`
without a business-role badge until an operator is selected, otherwise displays
the selected operator and `effective_role`. Navigation and direct page access
render the permissions UI only for `effective_role === "super_admin"`.

The page uses the real operator administration endpoints. It lists all
same-department operators and supports a basic create/edit form with the fixed
module matrix. Mutations refetch authoritative server data; conflicts refetch
and report the error. If the current operator loses access, the shell refreshes
auth state and returns to truthful login/selection state.

## Verification

Focused API tests prove response fields do not affect authorization behavior.
Focused Web tests cover header/nav/page gating, CRUD contract payloads, matrices,
error recovery, and current-session invalidation. Typecheck, lint, build, and
scope checks run before reporting readiness. This specification is deliberately
uncommitted until the Task 5 freeze request.
