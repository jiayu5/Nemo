import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import type { Session, ImageRef } from "../types";

vi.mock("./MessageImages", () => ({ MessageImages: () => <div>Image previews</div> }));
afterEach(cleanup);
const session: Session = { session_id: "one", workspace: "/project", model: null, approval_mode: "ask",
  created_at: "", updated_at: "", message_count: 0 };
const props = { selected: session, models: [], activeRun: null, prompt: "", onPromptChange: vi.fn(),
  onSubmit: vi.fn(), onStop: vi.fn(), onModeChange: vi.fn(), onModelChange: vi.fn() };
const image: ImageRef = { asset_id: "a".repeat(64), mime_type: "image/png", size_bytes: 10, width: 2, height: 2 };

it("allows an image-only message but blocks empty messages and unfinished uploads", () => {
  const view = render(<Composer {...props} />);
  expect(screen.getByRole("button", { name: "Run task" })).toBeDisabled();
  view.rerender(<Composer {...props} images={[image]} />);
  expect(screen.getByRole("button", { name: "Run task" })).toBeEnabled();
  view.rerender(<Composer {...props} images={[image]} uploading />);
  expect(screen.getByRole("button", { name: "Run task" })).toBeDisabled();
});

it("selects multiple images and supports clipboard and drop input without per-image text", () => {
  const onAddImages = vi.fn();
  const onRemoveImage = vi.fn();
  const view = render(<Composer {...props} images={[image]} onAddImages={onAddImages} onRemoveImage={onRemoveImage} />);
  const files = [new File(["a"], "a.png", { type: "image/png" }), new File(["b"], "b.png", { type: "image/png" })];
  fireEvent.change(screen.getByLabelText("Attach images"), { target: { files } });
  expect(onAddImages).toHaveBeenLastCalledWith(files);
  expect(screen.getAllByRole("textbox")).toHaveLength(1);
  fireEvent.paste(screen.getByRole("textbox"), { clipboardData: { items: [{ kind: "file", getAsFile: () => files[0] }] } });
  expect(onAddImages).toHaveBeenLastCalledWith([files[0]]);
  fireEvent.drop(view.container.querySelector(".composer-surface")!, { dataTransfer: { files } });
  expect(onAddImages).toHaveBeenLastCalledWith(files);
  fireEvent.click(screen.getByRole("button", { name: "Remove image 1" }));
  expect(onRemoveImage).toHaveBeenCalledWith(0);
});
