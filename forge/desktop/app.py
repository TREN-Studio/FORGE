from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

from forge import __version__
from forge.desktop.runtime import DesktopBootStatus, boot_status, run_prompt


BG = "#080808"
SURFACE = "#101010"
PANEL = "#151515"
BORDER = "#262626"
TEXT = "#F2F0EC"
MUTED = "#8A837B"
ACCENT = "#FF6B1A"
ACCENT_SOFT = "#FFAA3C"
SUCCESS = "#65D46E"


class ForgeDesktopApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"FORGE Desktop v{__version__}")
        self.root.geometry("1320x860")
        self.root.minsize(1080, 720)
        self.root.configure(bg=BG)

        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._busy = False
        self._operator_mode = tk.BooleanVar(value=True)
        self._status_text = tk.StringVar(value="Booting FORGE...")
        self._mode_text = tk.StringVar(value="Operator Brain")

        self._configure_window()
        self._build_layout()
        self.root.after(120, self._boot)
        self.root.after(120, self._drain_queue)

    def run(self) -> None:
        self.root.mainloop()

    def _configure_window(self) -> None:
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

    def _build_layout(self) -> None:
        header = tk.Frame(self.root, bg=BG, padx=28, pady=22)
        header.grid(row=0, column=0, sticky="nsew")
        header.grid_columnconfigure(1, weight=1)

        brand = tk.Label(
            header,
            text="FORGE",
            fg=ACCENT,
            bg=BG,
            font=("Bahnschrift", 28, "bold"),
        )
        brand.grid(row=0, column=0, sticky="w")

        meta = tk.Frame(header, bg=BG)
        meta.grid(row=0, column=1, sticky="e")
        tk.Label(
            meta,
            text="Desktop Operator",
            fg=TEXT,
            bg=BG,
            font=("Segoe UI Semibold", 11),
        ).grid(row=0, column=0, sticky="e")
        tk.Label(
            meta,
            textvariable=self._status_text,
            fg=MUTED,
            bg=BG,
            font=("Consolas", 10),
        ).grid(row=1, column=0, sticky="e", pady=(4, 0))

        shell = tk.Frame(self.root, bg=BG, padx=28, pady=0)
        shell.grid(row=1, column=0, sticky="nsew")
        shell.grid_columnconfigure(0, weight=0)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)

        side = tk.Frame(shell, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        side.grid(row=0, column=0, sticky="nsw", ipadx=12)
        side.configure(width=360)
        side.grid_propagate(False)

        body = tk.Frame(shell, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        body.grid(row=0, column=1, sticky="nsew", padx=(18, 0))
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self._build_sidebar(side)
        self._build_chat(body)

    def _build_sidebar(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=SURFACE, padx=22, pady=22)
        top.pack(fill="x")

        tk.Label(
            top,
            text="SERIOUS OPERATOR",
            fg=ACCENT_SOFT,
            bg=SURFACE,
            font=("Consolas", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            top,
            text="FORGE is built to run on-device, choose the strongest model path, and answer without pretending execution happened when it did not.",
            fg=TEXT,
            bg=SURFACE,
            justify="left",
            wraplength=300,
            font=("Segoe UI", 12),
        ).pack(anchor="w", pady=(16, 0))

        stat_box = tk.Frame(parent, bg=PANEL, padx=18, pady=18)
        stat_box.pack(fill="x", padx=22, pady=(6, 0))
        tk.Label(stat_box, text="MODE", fg=MUTED, bg=PANEL, font=("Consolas", 9, "bold")).pack(anchor="w")
        tk.Label(stat_box, textvariable=self._mode_text, fg=TEXT, bg=PANEL, font=("Segoe UI Semibold", 15)).pack(anchor="w", pady=(6, 0))

        toggle = tk.Checkbutton(
            parent,
            text="Operator Brain Locked",
            variable=self._operator_mode,
            command=self._toggle_mode,
            fg=TEXT,
            bg=SURFACE,
            activebackground=SURFACE,
            activeforeground=TEXT,
            selectcolor=PANEL,
            padx=22,
            pady=12,
            font=("Segoe UI", 11),
            state="disabled",
        )
        toggle.pack(anchor="w")

        message_box = tk.Frame(parent, bg=SURFACE, padx=22, pady=12)
        message_box.pack(fill="both", expand=True)

        tk.Label(
            message_box,
            text="BOOT NOTES",
            fg=ACCENT_SOFT,
            bg=SURFACE,
            font=("Consolas", 10, "bold"),
        ).pack(anchor="w")
        self.notes = ScrolledText(
            message_box,
            bg=BG,
            fg=MUTED,
            insertbackground=TEXT,
            relief="flat",
            borderwidth=0,
            height=16,
            wrap="word",
            font=("Consolas", 10),
            padx=14,
            pady=14,
        )
        self.notes.pack(fill="both", expand=True, pady=(10, 0))
        self.notes.insert("end", "Preparing runtime...\n")
        self.notes.configure(state="disabled")

    def _build_chat(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=SURFACE, padx=24, pady=20)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        tk.Label(
            top,
            text="Operator Mission Console",
            fg=TEXT,
            bg=SURFACE,
            font=("Bahnschrift", 22, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            top,
            text="Direct chat mode is removed. FORGE must answer through the operator path only.",
            fg=MUTED,
            bg=SURFACE,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.chat = ScrolledText(
            parent,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            borderwidth=0,
            wrap="word",
            font=("Segoe UI", 11),
            padx=18,
            pady=18,
        )
        self.chat.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 18))
        self.chat.insert(
            "end",
            "FORGE operator console initialized.\n"
            "Give FORGE a concrete mission to inspect, analyze, edit, run, browse, or publish.\n\n",
        )
        self.chat.configure(state="disabled")

        composer = tk.Frame(parent, bg=SURFACE, padx=24, pady=20)
        composer.grid(row=2, column=0, sticky="ew")
        composer.grid_columnconfigure(0, weight=1)

        self.input = tk.Text(
            composer,
            height=5,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            borderwidth=0,
            wrap="word",
            font=("Segoe UI", 11),
            padx=14,
            pady=14,
        )
        self.input.grid(row=0, column=0, sticky="ew")
        self.input.bind("<Control-Return>", self._send_from_event)

        controls = tk.Frame(composer, bg=SURFACE)
        controls.grid(row=0, column=1, sticky="ns", padx=(14, 0))

        self.send_button = tk.Button(
            controls,
            text="Send",
            command=self._send,
            bg=ACCENT,
            fg="black",
            activebackground=ACCENT_SOFT,
            activeforeground="black",
            relief="flat",
            padx=20,
            pady=14,
            font=("Segoe UI Semibold", 11),
            cursor="hand2",
        )
        self.send_button.pack(fill="x")

        tk.Button(
            controls,
            text="Clear",
            command=self._clear_chat,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief="flat",
            padx=20,
            pady=12,
            font=("Segoe UI", 10),
            cursor="hand2",
        ).pack(fill="x", pady=(10, 0))

    def _boot(self) -> None:
        def runner() -> None:
            try:
                status = boot_status()
                self._queue.put(("boot", status.summary))
                self._queue.put(
                    (
                        "boot",
                        f"Providers online: {status.providers} | Models online: {status.models_online}",
                    )
                )
                if status.models_online > 0:
                    self._queue.put(("status", "FORGE runtime is ready."))
                else:
                    self._queue.put(("status", "FORGE booted, but no live models were detected."))
            except Exception as exc:
                self._queue.put(("status", f"Boot failed: {exc}"))

        threading.Thread(target=runner, daemon=True).start()

    def _toggle_mode(self) -> None:
        self._mode_text.set("Operator Brain")

    def _send_from_event(self, event) -> str:
        self._send()
        return "break"

    def _send(self) -> None:
        if self._busy:
            return

        prompt = self.input.get("1.0", "end").strip()
        if not prompt:
            return

        self.input.delete("1.0", "end")
        self._append_chat("You", prompt, TEXT)
        self._append_note("Dispatching prompt to runtime...")
        self._set_busy(True)

        def worker() -> None:
            try:
                answer = run_prompt(prompt, use_operator=self._operator_mode.get())
                self._queue.put(("answer", answer))
            except Exception as exc:
                self._queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.send_button.configure(text="Working...", state="disabled")
            self._status_text.set("Running live model call...")
        else:
            self.send_button.configure(text="Send", state="normal")
            self._status_text.set("Ready for the next task.")

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if kind == "boot":
                self._append_note(payload)
            elif kind == "status":
                self._status_text.set(payload)
                self._append_note(payload)
            elif kind == "answer":
                self._append_chat("FORGE", payload, ACCENT_SOFT)
                self._append_note("Response completed successfully.")
                self._set_busy(False)
            elif kind == "error":
                self._append_chat("FORGE", payload, "#FF7373")
                self._append_note(f"Execution error: {payload}")
                self._set_busy(False)

        self.root.after(120, self._drain_queue)

    def _append_chat(self, speaker: str, message: str, color: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{speaker}\n", (speaker,))
        self.chat.insert("end", f"{message}\n\n")
        self.chat.tag_configure(speaker, foreground=color, font=("Segoe UI Semibold", 11))
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _append_note(self, line: str) -> None:
        self.notes.configure(state="normal")
        self.notes.insert("end", f"{line}\n")
        self.notes.configure(state="disabled")
        self.notes.see("end")

    def _clear_chat(self) -> None:
        if self._busy:
            messagebox.showinfo("FORGE", "Wait for the current response to finish.")
            return
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.insert(
            "end",
            "FORGE Desktop initialized.\n"
            "Ask for research, planning, code help, or execution-oriented guidance.\n\n",
        )
        self.chat.configure(state="disabled")
