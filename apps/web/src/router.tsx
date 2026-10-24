import {
  createHashHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";

import { AccountPage } from "@/routes/accounts/account";
import { AgeingPage } from "@/routes/accounts/ageing";
import { PdcPage } from "@/routes/accounts/pdc";
import { PdcEntryRoute } from "@/routes/accounts/pdc-entry";
import { PdcRegisterPage } from "@/routes/accounts/pdcs";
import { ChartPage } from "@/routes/accounts/chart";
import { PeriodsPage } from "@/routes/accounts/periods";
import { TrialBalancePage } from "@/routes/accounts/trial-balance";
import { VoucherPage } from "@/routes/accounts/voucher";
import { VoucherRoute } from "@/routes/accounts/voucher-entry";
import { VouchersPage } from "@/routes/accounts/vouchers";
import { CatalogSettingsPage } from "@/routes/catalog/catalog-settings";
import { NewProductPage } from "@/routes/catalog/new-product";
import { ProductPage } from "@/routes/catalog/product";
import { ProductsPage } from "@/routes/catalog/products";
import { ComingSoon } from "@/routes/coming-soon";
import { NewPartyPage } from "@/routes/parties/new-party";
import { PartiesPage } from "@/routes/parties/parties";
import { PartyPage } from "@/routes/parties/party";
import { PricingSettingsPage } from "@/routes/pricing/pricing-settings";
import { PurchaseDocumentPage } from "@/routes/purchasing/purchase-document";
import { PurchaseEntryRoute } from "@/routes/purchasing/purchase-entry";
import { PurchasesPage } from "@/routes/purchasing/purchases";
import { SalesDocumentPage } from "@/routes/sales/sales-document";
import { SalesEntryRoute } from "@/routes/sales/sales-entry";
import { SalesPage } from "@/routes/sales/sales";
import { DashboardPage } from "@/routes/dashboard";
import { IndexRedirect } from "@/routes/index-redirect";
import { StockDocumentsPage } from "@/routes/inventory/documents";
import { ItemLedgerPage } from "@/routes/inventory/item-ledger";
import { NegativeStockPage, StockPage } from "@/routes/inventory/stock";
import { StockDocumentPage } from "@/routes/inventory/stock-document";
import { StockEntryRoute } from "@/routes/inventory/stock-entry";
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
const stockRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/stock",
  component: StockPage,
});
const negativeStockRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/negative",
  component: NegativeStockPage,
});
const itemLedgerRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/items/$variantId",
  component: ItemLedgerPage,
});
const stockDocumentsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/documents",
  component: StockDocumentsPage,
});
const newStockDocumentRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/documents/new/$kind",
  component: StockEntryRoute,
});
const stockDocumentRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inventory/documents/$docId",
  component: StockDocumentPage,
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
/** Voucher entry: the accounting vouchers (M1.5); sales and purchase keys still show "coming soon". */
const voucherRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/vouchers/$kind",
  component: VoucherRoute,
});
const salesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/sales",
  component: SalesPage,
});
/** ``from``: start from an earlier document (a quotation to order, an order to deliver or invoice…). */
const newSalesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/sales/new/$kind",
  validateSearch: (search: Record<string, unknown>): { from?: string } =>
    typeof search.from === "string" ? { from: search.from } : {},
  component: SalesEntryRoute,
});
const salesDocRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/sales/$docId",
  component: SalesDocumentPage,
});
const purchasesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/purchases",
  component: PurchasesPage,
});
/** ``from``: start from an earlier document (an order to receive, a receipt to bill, a bill to return). */
const newPurchaseRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/purchases/new/$kind",
  validateSearch: (search: Record<string, unknown>): { from?: string } =>
    typeof search.from === "string" ? { from: search.from } : {},
  component: PurchaseEntryRoute,
});
const purchaseRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/purchases/$docId",
  component: PurchaseDocumentPage,
});
const accountsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts",
  component: ChartPage,
});
const vouchersRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/vouchers",
  component: VouchersPage,
});
const voucherViewRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/vouchers/$voucherId",
  component: VoucherPage,
});
const trialBalanceRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/trial-balance",
  component: TrialBalancePage,
});
const chequesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/cheques",
  component: PdcRegisterPage,
});
/** ``direction``: received (the default) or issued. */
const newChequeRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/cheques/new",
  validateSearch: (search: Record<string, unknown>): { direction?: string } =>
    typeof search.direction === "string" ? { direction: search.direction } : {},
  component: PdcEntryRoute,
});
const chequeRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/cheques/$pdcId",
  component: PdcPage,
});
const ageingRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/ageing",
  component: AgeingPage,
});
const periodsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/periods",
  component: PeriodsPage,
});
const accountRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/accounts/$accountId",
  component: AccountPage,
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
    stockRoute,
    negativeStockRoute,
    itemLedgerRoute,
    stockDocumentsRoute,
    newStockDocumentRoute,
    stockDocumentRoute,
    partiesRoute,
    newPartyRoute,
    partyRoute,
    catalogSettingsRoute,
    pricingSettingsRoute,
    salesRoute,
    newSalesRoute,
    salesDocRoute,
    purchasesRoute,
    newPurchaseRoute,
    purchaseRoute,
    voucherRoute,
    accountsRoute,
    vouchersRoute,
    voucherViewRoute,
    trialBalanceRoute,
    chequesRoute,
    newChequeRoute,
    chequeRoute,
    ageingRoute,
    periodsRoute,
    accountRoute,
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
