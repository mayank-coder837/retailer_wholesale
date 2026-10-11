import {
  createHashHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";

import { ComingSoon } from "@/routes/coming-soon";
import { DashboardPage } from "@/routes/dashboard";
import { IndexRedirect } from "@/routes/index-redirect";
import { LoginPage } from "@/routes/login";
import { RolesPage } from "@/routes/roles";
import { SignupPage } from "@/routes/signup";
import { ThisPcPage } from "@/routes/this-pc";
import { AppShell } from "@/shell/app-shell";

const rootRoute = createRootRoute({ component: Outlet });

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: IndexRedirect });
const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: "/login", component: LoginPage });
const signupRoute = createRoute({ getParentRoute: () => rootRoute, path: "/signup", component: SignupPage });

/** Everything below needs a signed-in user; AppShell sends others to /login. */
const appRoute = createRoute({ getParentRoute: () => rootRoute, id: "app", component: AppShell });

/** A Phase 1 area that is not built yet. Generic so the path stays a literal type for typed links. */
const page = <P extends string>(path: P, titleKey: string) =>
  createRoute({ getParentRoute: () => appRoute, path, component: () => <ComingSoon titleKey={titleKey} /> });

const dashboardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/dashboard",
  component: DashboardPage,
});
const rolesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/settings/roles",
  component: RolesPage,
});
const thisPcRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/settings/this-pc",
  component: ThisPcPage,
});
const voucherRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/vouchers/$kind",
  component: function Voucher() {
    const { kind } = voucherRoute.useParams();
    return <ComingSoon titleKey={`vouchers.${kind}`} />;
  },
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  signupRoute,
  appRoute.addChildren([
    dashboardRoute,
    rolesRoute,
    thisPcRoute,
    voucherRoute,
    page("/sales", "nav.sales"),
    page("/purchases", "nav.purchases"),
    page("/inventory", "nav.inventory"),
    page("/accounts", "nav.accounts"),
    page("/reports", "nav.reports"),
  ]),
]);

/** Hash history: works the same from Electron (file://), the local API and a CDN without server rewrites. */
export function createAppRouter() {
  return createRouter({ routeTree, history: createHashHistory() });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
