#!/usr/bin/env python3
# combine_pdf.py
"""
PDF Merger Application using Textual.
Paste / drag‑and‑drop PDF paths  → reorder with Shift+↑/↓ → Save/Load list → Merge.
Shows current working directory at the top. If you paste / drop a **folder path**, the
application changes to that directory while keeping the existing PDF list (paths are
stored as absolute, so they remain valid).
During merge the Merge button shows *Merging…* and switches to *Merged: <file>* when
finished (background thread keeps UI responsive).
"""

import os
import re
import json
import threading
from datetime import datetime
from functools import partial

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Button, ListView, ListItem, Static
from textual.containers import Horizontal
from textual.events import Paste
from textual.reactive import reactive
from pypdf import PdfReader, PdfWriter

JSON_FILE = "combine_pdf.json"

class PdfMergerApp(App):
    """Textual TUI for merging PDFs and displaying current working directory."""

    CSS = """
#cwd-display {
    dock: top;
    content-align: left middle;
    padding: 1;
}
#button-row {
    layout: horizontal;
    padding: 1;
}
#button-row Button {
    min-width: 14;
    overflow-x: auto;
}
"""

    files: list[str] = reactive([])
    cwd: str = reactive(os.getcwd())  # track current directory

    BINDINGS = [
        ("shift+up", "move_up", "Move selected up"),
        ("shift+down", "move_down", "Move selected down"),
        ("m", "merge", "Merge PDFs"),
        ("s", "save_list", "Save list"),
        ("l", "load_list", "Load list"),
        ("q", "quit", "Quit"),
    ]

    # ─── Layout ──────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static(f"Current directory: {self.cwd}", id="cwd-display")
        yield Header(show_clock=True)
        yield Static("📋 Paste or drop PDF paths (quoted or unquoted) / folder paths to change directory")
        yield ListView(id="file-list")
        yield Horizontal(
            Button("Merge (m)", id="merge-button"),
            Button("Save (s)", id="save-button"),
            Button("Load (l)", id="load-button"),
            id="button-row",
        )
        yield Footer()

    # ─── Helper: refresh listview ────────────────────────────────────────────
    def _refresh_list(self, new_index: int | None = None) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        for i, path in enumerate(self.files):
            label = f"> {path}" if i == new_index else f"  {path}"
            item = ListItem(Static(label))
            if i == new_index:
                item.styles.reverse = True
            lv.append(item)
        if new_index is not None:
            lv.index = new_index
            lv.focus()

    def _update_cwd_display(self) -> None:
        self.query_one("#cwd-display", Static).update(f"Current directory: {self.cwd}")

    # ─── Paste / Drop ────────────────────────────────────────────────────────
    async def on_paste(self, event: Paste) -> None:
        txt = event.text.strip()
        pattern = r'"([^\"]+)"|(\S+)'
        tokens = [q or u for q, u in re.findall(pattern, txt)]
        if not tokens:
            return
        # Handle directories (take last dir in tokens if multiple)
        dir_tokens = [t for t in tokens if os.path.isdir(t)]
        if dir_tokens:
            os.chdir(dir_tokens[-1])
            self.cwd = os.getcwd()
            self._update_cwd_display()
            # remove dir tokens from list so they are not treated as PDFs
            tokens = [t for t in tokens if t not in dir_tokens]
        # Add PDF files
        new = [p for p in tokens if p.lower().endswith(".pdf")]
        if new:
            # store absolute paths to be independent of cwd changes
            abs_new = [os.path.abspath(p) for p in new]
            self.files.extend(abs_new)
            self._refresh_list(len(self.files) - len(abs_new))

    # ─── Reorder ----------------------------------------------------------------
    def action_move_up(self) -> None:
        lv = self.query_one(ListView)
        i = lv.index
        if i > 0:
            self.files[i - 1], self.files[i] = self.files[i], self.files[i - 1]
            self._refresh_list(i - 1)

    def action_move_down(self) -> None:
        lv = self.query_one(ListView)
        i = lv.index
        if i < len(self.files) - 1:
            self.files[i + 1], self.files[i] = self.files[i], self.files[i + 1]
            self._refresh_list(i + 1)

    # ─── Save / Load ------------------------------------------------------------
    def action_save_list(self) -> None:
        btn = self.query_one("#save-button", Button)
        try:
            with open(JSON_FILE, "w", encoding="utf-8") as f:
                json.dump(self.files, f, ensure_ascii=False, indent=2)
            btn.label = f"Saved: {JSON_FILE}"
        except Exception as e:
            btn.label = f"Save err: {e}"
        btn.refresh(layout=True)

    def action_load_list(self) -> None:
        btn = self.query_one("#load-button", Button)
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception as e:
            btn.label = f"Load err: {e}"
            btn.refresh(layout=True)
            return
        missing = [p for p in loaded if not os.path.exists(p)]
        if missing:
            btn.label = f"Missing: {os.path.basename(missing[0])}"
            btn.refresh(layout=True)
            return
        self.files = loaded
        self._refresh_list(0 if self.files else None)
        btn.label = f"Loaded: {JSON_FILE}"
        btn.refresh(layout=True)

    # ─── Merge ------------------------------------------------------------------
    def action_merge(self) -> None:
        btn = self.query_one("#merge-button", Button)
        if not self.files:
            self.bell(); return
        btn.label = "Merging…"
        btn.refresh(layout=True)
        threading.Thread(target=self._do_merge, args=(self.files.copy(),), daemon=True).start()

    def _do_merge(self, pdf_paths: list[str]) -> None:
        writer = PdfWriter()
        try:
            for p in pdf_paths:
                reader = PdfReader(p)
                for pg in reader.pages:
                    writer.add_page(pg)
            out = f"combined-{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
            with open(out, "wb") as f:
                writer.write(f)
        except Exception as e:
            self.call_from_thread(partial(self._merge_finished, error=str(e)))
            return
        self.call_from_thread(partial(self._merge_finished, output=out))

    def _merge_finished(self, output: str | None = None, error: str | None = None) -> None:
        btn = self.query_one("#merge-button", Button)
        btn.label = f"Error: {error}" if error else f"Merged: {output}"
        btn.refresh(layout=True)

if __name__ == "__main__":
    PdfMergerApp().run()