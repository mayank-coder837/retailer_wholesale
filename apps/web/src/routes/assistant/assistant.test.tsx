import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { shopApi } from "@/test/shop-api";

const action = (extra: Record<string, unknown> = {}) => ({
  id: "a1",
  trace_id: "t1",
  tool: "find_party",
  risk: "read",
  status: "done",
  prompt: null,
  args: {},
  result: {},
  error_code: null,
  decided_at: null,
  created_at: "2026-11-24T08:00:00Z",
  ...extra,
});

const DRAFT = action({
  id: "d1",
  tool: "create_draft_receipt",
  risk: "draft",
  prompt: "Received 5000 cash from Ali Traders against INV-1093",
  result: {
    document_id: "v1",
    doc_type: "receipt",
    party: "Ali Traders",
    amount: "5000.0000",
    bill: "INV-1093",
    settled: "5000.0000",
    on_account: "0.0000",
  },
});

const PENDING = action({
  id: "p1",
  tool: "post_voucher",
  risk: "commit",
  status: "pending",
  args: { document_id: "v1" },
});

const base = { signedIn: true, "GET /ai/actions": { status: 200, body: [] } };

describe("Assistant", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
    window.location.hash = "#/assistant";
  });

  it("turns a sentence into a draft and says it is only a draft", async () => {
    const { api, calls } = shopApi({
      ...base,
      "POST /ai/chat": {
        status: 200,
        body: { trace_id: "t1", message: null, message_facts: {}, actions: [DRAFT] },
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.type(
      await screen.findByLabelText("What would you like to do?"),
      "Received 5000 cash from Ali Traders against INV-1093",
    );
    await user.click(screen.getByRole("button", { name: "Ask" }));

    const card = await screen.findByRole("article", { name: "Draft receipt" });
    expect(within(card).getByText("Ali Traders: 5,000.00")).toBeInTheDocument();
    expect(within(card).getByText(/Settles INV-1093 for 5,000.00/)).toBeInTheDocument();
    expect(within(card).getByText(/only a draft/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open the document" })).toHaveAttribute(
      "href",
      "/#/accounts/vouchers/v1",
    );
    const chat = calls.find((c) => c.path === "/ai/chat");
    expect(chat?.body).toEqual({
      business_id: "b1",
      text: "Received 5000 cash from Ali Traders against INV-1093",
      lang: "en",
    });
  });

  it("asks before posting: a waiting action can be confirmed", async () => {
    const { api, calls } = shopApi({
      ...base,
      "GET /ai/actions": { status: 200, body: [PENDING] },
      "POST /ai/actions/p1/confirm": {
        status: 200,
        body: { ...PENDING, status: "confirmed", result: { number: "RV-1" } },
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    const card = await screen.findByRole("article", { name: "Post to the books" });
    expect(within(card).getByText(/Will post this draft/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/ai/actions/p1/confirm")).toBe(true));
    expect(calls.find((c) => c.path === "/ai/actions/p1/confirm")?.body).toEqual({ business_id: "b1" });
  });

  it("rejects a waiting action", async () => {
    const { api, calls } = shopApi({
      ...base,
      "GET /ai/actions": { status: 200, body: [PENDING] },
      "POST /ai/actions/p1/reject": { status: 200, body: { ...PENDING, status: "rejected" } },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/ai/actions/p1/reject")).toBe(true));
  });

  it("says plainly when a name is unknown, and when someone may not do something", async () => {
    const { api } = shopApi({
      ...base,
      "POST /ai/chat": {
        status: 200,
        body: {
          trace_id: "t2",
          message: "party_not_found",
          message_facts: { name: "Zed Co", matches: [] },
          actions: [
            action({ id: "f1", status: "done", result: { parties: [] } }),
            action({
              id: "n1",
              tool: "create_draft_receipt",
              risk: "draft",
              status: "denied",
              error_code: "permission_denied",
              result: { permission: "receipt.create" },
            }),
          ],
        },
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.type(await screen.findByLabelText("What would you like to do?"), "Received 100 from Zed Co");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/I could not find “Zed Co”/)).toBeInTheDocument();
    const denied = screen.getByRole("article", { name: "Draft receipt" });
    expect(denied).toHaveTextContent("You do not have permission to do this. (receipt.create)");
  });

  it("works in Arabic", async () => {
    await setLanguage("ar");
    const { api } = shopApi(base);
    render(<App api={api} />);
    expect(await screen.findByRole("heading", { name: "المساعد" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "اسأل" })).toBeInTheDocument();
  });
});
