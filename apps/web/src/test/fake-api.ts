import { createApiClient } from "@smartbiz/api-client";

export interface Recorded {
  method: string;
  path: string;
  query: string;
  body: unknown;
  authorization: string | null;
}

type Handler = (body: unknown) => { status: number; body: unknown };

/** Use as a route's answer to make fetch reject, as it does when the server cannot be reached. */
export const UNREACHABLE = { status: -1, body: null } as const;

/** An API client whose fetch answers from a "METHOD /path" table and records every request. */
export function fakeApi(routes: Record<string, Handler | { status: number; body: unknown }>) {
  const calls: Recorded[] = [];
  const fetchImpl = (async (input: Request) => {
    const url = new URL(input.url);
    // An upload is a form: its fields are recorded, a file as "[file name]". (The text is read by hand: the test
    // environment's File is not the one the runtime's form parser accepts.)
    let body: unknown;
    const type = input.headers.get("Content-Type") ?? "";
    if (type.includes("multipart/form-data")) {
      const boundary = /boundary=(.+)$/.exec(type)?.[1] ?? "";
      const fields: Record<string, string> = {};
      for (const part of (await input.text()).split(`--${boundary}`)) {
        const name = /name="([^"]+)"/.exec(part)?.[1];
        if (!name) continue;
        const file = /filename="([^"]*)"/.exec(part)?.[1];
        const value = part.split(/\r?\n\r?\n/)[1]?.replace(/\r?\n$/, "") ?? "";
        fields[name] = file !== undefined ? `[file ${file}]` : value;
      }
      body = fields;
    } else {
      const text = await input.text();
      body = text ? JSON.parse(text) : undefined;
    }
    calls.push({
      method: input.method,
      path: url.pathname,
      query: url.search,
      body,
      authorization: input.headers.get("Authorization"),
    });
    const route = routes[`${input.method} ${url.pathname}`];
    const answer =
      typeof route === "function" ? route(body) : (route ?? { status: 404, body: { code: "not_found" } });
    if (answer.status === UNREACHABLE.status) throw new TypeError("Failed to fetch");
    if (answer.status === 204) return new Response(null, { status: 204 });
    // A string body is a page (a printed document), not JSON.
    if (typeof answer.body === "string")
      return new Response(answer.body, { status: answer.status, headers: { "content-type": "text/html" } });
    return new Response(JSON.stringify(answer.body), {
      status: answer.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return { api: createApiClient("http://api.test", fetchImpl), calls };
}
