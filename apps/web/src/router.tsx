import {
  createHashHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";

import { CatalogSettingsPage } from "@/routes/catalog/catalog-settings";
import { NewProductPage } from "@/routes/catalog/new-product";
import { ProductPage } from "@/routes/catalog/product";
import { ProductsPage } from "@/routes/catalog/products";
import { ComingSoon } from "@/routes/coming-soon";
import { NewPartyPage } from "@/routes/parties/new-party";
import { PartiesPage } from "@/routes/parties/parties";
import { PartyPage } from "@/routes/parties/party";
import { PricingSettingsPage } from "@/routes/pricing/pricing-settings";
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
const inventoryRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory",
  component: ProductsPage,
});
const newProductRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/products/new",
  component: NewProductPage,
});
const productRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/products/$productId",
  component: ProductPage,
});
const partiesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/parties",
  component: PartiesPage,
});
const newPartyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/parties/new",
  component: NewPartyPage,
});
const partyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/parties/$partyId",
  component: PartyPage,
});
const catalogSettingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/settings/catalog",
  component: CatalogSettingsPage,
});
const pricingSettingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/settings/pricing",
  component: PricingSettingsPage,
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
    inventoryRoute,
    newProductRoute,
    productRoute,
    partiesRoute,
    newPartyRoute,
    partyRoute,
    catalogSettingsRoute,
    pricingSettingsRoute,
    voucherRoute,
    page("/sales", "nav.sales"),
    page("/purchases", "nav.purchases"),
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
