import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { FileBrowser } from "./file-browser";

function fsHandlers() {
  return http.get("*/api/fs/list", ({ request }) => {
    const path = new URL(request.url).searchParams.get("path");
    if (path === "/home/u/agents") {
      return HttpResponse.json({
        path: "/home/u/agents",
        parent: "/home/u",
        entries: [
          { name: "agent.py", path: "/home/u/agents/agent.py", is_dir: false },
        ],
      });
    }
    // default: home
    return HttpResponse.json({
      path: "/home/u",
      parent: "/home",
      entries: [
        { name: "agents", path: "/home/u/agents", is_dir: true },
        { name: "notes.txt", path: "/home/u/notes.txt", is_dir: false },
      ],
    });
  });
}

describe("FileBrowser", () => {
  it("navigates into a folder and returns the chosen file's absolute path", async () => {
    server.use(fsHandlers());
    const onSelect = vi.fn();
    const onClose = vi.fn();
    renderWithProviders(
      <FileBrowser open onClose={onClose} onSelect={onSelect} />,
    );

    // Home listing
    await waitFor(() =>
      expect(screen.getByText("agents")).toBeInTheDocument(),
    );
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    // Nothing selected yet → confirm disabled
    expect(screen.getByRole("button", { name: /use this file/i })).toBeDisabled();

    // Descend into the folder
    fireEvent.click(screen.getByText("agents"));
    await waitFor(() =>
      expect(screen.getByText("agent.py")).toBeInTheDocument(),
    );

    // Select the file and confirm
    fireEvent.click(screen.getByText("agent.py"));
    const confirm = screen.getByRole("button", { name: /use this file/i });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);

    expect(onSelect).toHaveBeenCalledWith("/home/u/agents/agent.py");
    expect(onClose).toHaveBeenCalled();
  });

  it("selects a pasted file path directly in file mode", async () => {
    server.use(fsHandlers());
    const onSelect = vi.fn();
    const onClose = vi.fn();
    renderWithProviders(
      <FileBrowser open onClose={onClose} onSelect={onSelect} />,
    );
    await waitFor(() => expect(screen.getByText("agents")).toBeInTheDocument());

    const input = screen.getByLabelText("Path");
    fireEvent.change(input, {
      target: { value: "/home/u/agents/agent.py" },
    });
    // Single unified confirm button (the separate "Use this path" was merged in).
    fireEvent.click(screen.getByRole("button", { name: /use this file/i }));

    expect(onSelect).toHaveBeenCalledWith("/home/u/agents/agent.py");
    expect(onClose).toHaveBeenCalled();
  });

  it("confirms the typed directory path, not just the listed dir (ingest regression)", async () => {
    // The dir picker must send exactly the path shown in the (single, editable) field — the bug
    // was that "Use this folder" confirmed the currently-listed dir, so a pasted bundle path was
    // ignored and the parent dir was ingested, failing preflight while the CLI (exact path) passed.
    server.use(fsHandlers());
    const onSelect = vi.fn();
    renderWithProviders(
      <FileBrowser open onClose={vi.fn()} onSelect={onSelect} mode="directory" />,
    );
    await waitFor(() => expect(screen.getByText("agents")).toBeInTheDocument()); // home = /home/u
    const input = screen.getByLabelText("Path");
    fireEvent.change(input, { target: { value: "/home/u/bundles/mine" } });
    fireEvent.click(screen.getByRole("button", { name: /use this folder/i }));
    expect(onSelect).toHaveBeenCalledWith("/home/u/bundles/mine");
  });

  it("navigates to a pasted directory path with Go", async () => {
    server.use(fsHandlers());
    renderWithProviders(
      <FileBrowser open onClose={vi.fn()} onSelect={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByText("agents")).toBeInTheDocument());

    const input = screen.getByLabelText("Path");
    fireEvent.change(input, { target: { value: "/home/u/agents" } });
    fireEvent.click(screen.getByRole("button", { name: /^go$/i }));

    await waitFor(() =>
      expect(screen.getByText("agent.py")).toBeInTheDocument(),
    );
  });

  it("shows the listing error for an invalid pasted path", async () => {
    server.use(
      http.get("*/api/fs/list", ({ request }) => {
        const path = new URL(request.url).searchParams.get("path");
        if (path === "/does/not/exist")
          return HttpResponse.json({ detail: "no such dir" }, { status: 400 });
        return HttpResponse.json({
          path: "/home/u",
          parent: "/home",
          entries: [],
        });
      }),
    );
    renderWithProviders(
      <FileBrowser open onClose={vi.fn()} onSelect={vi.fn()} />,
    );
    const input = await screen.findByLabelText("Path");
    fireEvent.change(input, { target: { value: "/does/not/exist" } });
    fireEvent.click(screen.getByRole("button", { name: /^go$/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/Couldn’t list this directory/i),
      ).toBeInTheDocument(),
    );
  });

  it("surfaces a friendly error when listing fails", async () => {
    server.use(
      http.get("*/api/fs/list", () =>
        HttpResponse.json({ detail: "permission denied" }, { status: 400 }),
      ),
    );
    renderWithProviders(
      <FileBrowser open onClose={vi.fn()} onSelect={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByText(/Couldn’t list this directory/i)).toBeInTheDocument(),
    );
  });
});
