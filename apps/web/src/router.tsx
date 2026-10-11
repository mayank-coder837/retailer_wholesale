import {
  createHashHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";

import { HomePage } from "@/routes/home";
import { LoginPage } from "@/routes/login";
import { RolesPage } from "@/routes/roles";

const rootRoute = createRootRoute({ component: Outlet });

const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: HomePage });
const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: "/login", component: LoginPage });
const rolesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings/roles",
  component: RolesPage,
});

const routeTree = rootRoute.addChildren([homeRoute, loginRoute, rolesRoute]);

/** Hash history: works the same from Electron (file://), the local API and a CDN without server rewrites. */
export function createAppRouter() {
  return createRouter({ routeTree, history: createHashHistory() });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
