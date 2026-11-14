"""Simple GUI runner for Strategy C.

Lightweight (Tkinter, stdlib only):
- Select which MT5 symbols/pairs to trade
- Configure Strategy C parameters
- Start/stop the subprocess
- Stream logs into the UI

Run:
  py strategies/gui_strategy_c.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from typing import List, Optional
from tkinter import ttk


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
STRATEGY_PATH = os.path.join(REPO_ROOT, "strategies", "strategy_c_news_volatility.py")


MAJOR_28 = [
    "EURUSD",
    "USDJPY",
    "GBPUSD",
    "AUDUSD",
    "NZDUSD",
    "USDCAD",
    "USDCHF",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "NZDJPY",
    "CADJPY",
    "CHFJPY",
    "EURGBP",
    "EURAUD",
    "EURNZD",
    "EURCAD",
    "EURCHF",
    "GBPAUD",
    "GBPNZD",
    "GBPCAD",
    "GBPCHF",
    "AUDNZD",
    "AUDCAD",
    "AUDCHF",
    "NZDCAD",
    "NZDCHF",
    "CADCHF",
]


def _tokens(value: str) -> List[str]:
    return [t.strip().upper() for t in value.replace(",", " ").split() if t.strip()]


class StrategyCGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VANTA - Strategy C GUI")
        self.geometry("1020x720")

        self.proc: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None

        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=10, pady=10)

        # ---- Top: symbols + parameters
        top = ttk.Frame(root)
        top.pack(fill="x", expand=False)

        symbols_box = ttk.LabelFrame(top, text="Symbols / Pairs")
        symbols_box.pack(side="left", fill="both", expand=True, padx=(0, 8))

        params_box = ttk.LabelFrame(top, text="Parameters")
        params_box.pack(side="left", fill="both", expand=True)

        # --- Symbols selector
        self.custom_symbols_var = tk.StringVar(value="EURUSD")
        self.currencies_override_var = tk.StringVar(value="")

        ttk.Label(symbols_box, text="Select pairs (multi-select):").pack(anchor="w", padx=8, pady=(8, 4))

        list_frame = ttk.Frame(symbols_box)
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self.symbol_list = tk.Listbox(list_frame, selectmode="extended", height=10)
        for s in MAJOR_28:
            self.symbol_list.insert("end", s)
        self.symbol_list.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.symbol_list.yview)
        scroll.pack(side="right", fill="y")
        self.symbol_list.configure(yscrollcommand=scroll.set)

        # Default selection: EURUSD
        try:
            idx = MAJOR_28.index("EURUSD")
            self.symbol_list.selection_set(idx)
            self.symbol_list.see(idx)
        except ValueError:
            pass

        sym_btns = ttk.Frame(symbols_box)
        sym_btns.pack(fill="x", expand=False, padx=8, pady=(0, 6))

        def select_all() -> None:
            self.symbol_list.selection_set(0, "end")

        def clear_sel() -> None:
            self.symbol_list.selection_clear(0, "end")

        ttk.Button(sym_btns, text="Select all", command=select_all).pack(side="left", padx=(0, 6))
        ttk.Button(sym_btns, text="Clear", command=clear_sel).pack(side="left")

        ttk.Label(symbols_box, text="Custom symbols (space-separated, optional):").pack(anchor="w", padx=8)
        ttk.Entry(symbols_box, textvariable=self.custom_symbols_var, width=48).pack(
            anchor="w", padx=8, pady=(0, 8)
        )

        ttk.Label(symbols_box, text="Currencies override (optional, e.g. USD EUR):").pack(anchor="w", padx=8)
        ttk.Entry(symbols_box, textvariable=self.currencies_override_var, width=48).pack(
            anchor="w", padx=8, pady=(0, 10)
        )

        # --- Parameters layout (two columns)
        left = ttk.Frame(params_box)
        right = ttk.Frame(params_box)
        left.pack(side="left", fill="both", expand=True, padx=(8, 8), pady=8)
        right.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        def grid_row(frame: ttk.Frame, r: int, label: str, var: tk.StringVar, default: str, width: int = 16) -> None:
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w", padx=(0, 6), pady=3)
            var.set(default)
            ttk.Entry(frame, textvariable=var, width=width).grid(row=r, column=1, sticky="w", pady=3)

        self.risk_pct_var = tk.StringVar()
        self.sl_pips_var = tk.StringVar()
        self.tp_pips_var = tk.StringVar()
        self.window_hours_var = tk.StringVar()
        self.lead_seconds_var = tk.StringVar()
        self.lookback_minutes_var = tk.StringVar()
        self.distance_mult_var = tk.StringVar()
        self.min_distance_pips_var = tk.StringVar()
        self.lot_var = tk.StringVar()
        self.magic_var = tk.StringVar()
        self.poll_seconds_var = tk.StringVar()
        self.cancel_pending_after_minutes_var = tk.StringVar()
        self.close_position_after_minutes_var = tk.StringVar()
        self.test_hold_seconds_var = tk.StringVar(value="15")

        grid_row(left, 0, "Risk %", self.risk_pct_var, "0.25")
        grid_row(left, 1, "SL (pips)", self.sl_pips_var, "20")
        grid_row(left, 2, "TP (pips)", self.tp_pips_var, "30")
        grid_row(left, 3, "Fixed lot (if risk%=0)", self.lot_var, "0.01")
        grid_row(left, 4, "Magic (base)", self.magic_var, "7003001")
        grid_row(left, 5, "Poll seconds", self.poll_seconds_var, "1")

        grid_row(right, 0, "Window (hours)", self.window_hours_var, "24")
        grid_row(right, 1, "Lead (seconds)", self.lead_seconds_var, "90")
        grid_row(right, 2, "Lookback (minutes)", self.lookback_minutes_var, "60")
        grid_row(right, 3, "Distance mult", self.distance_mult_var, "0.5")
        grid_row(right, 4, "Min distance (pips)", self.min_distance_pips_var, "5")
        grid_row(right, 5, "Cancel pending after (min)", self.cancel_pending_after_minutes_var, "3")
        grid_row(right, 6, "Close position after (min, 0=off)", self.close_position_after_minutes_var, "0")

        self.impact_var = tk.StringVar(value="High")
        self.risk_base_var = tk.StringVar(value="balance")

        ttk.Label(right, text="Impact").grid(row=7, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Combobox(right, textvariable=self.impact_var, values=["High", "Medium", "Low", "Holiday"], width=14).grid(
            row=7, column=1, sticky="w", pady=3
        )

        ttk.Label(right, text="Risk base").grid(row=8, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Combobox(right, textvariable=self.risk_base_var, values=["balance", "equity"], width=14).grid(
            row=8, column=1, sticky="w", pady=3
        )

        ttk.Label(right, text="Test hold (sec)").grid(row=9, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(right, textvariable=self.test_hold_seconds_var, width=16).grid(row=9, column=1, sticky="w", pady=3)

        self.dry_run_var = tk.BooleanVar(value=False)
        self.test_entry_var = tk.BooleanVar(value=False)
        self.no_oco_var = tk.BooleanVar(value=False)
        self.shared_magic_var = tk.BooleanVar(value=False)

        opts = ttk.Frame(params_box)
        opts.pack(fill="x", expand=False, padx=8, pady=(0, 8))
        ttk.Checkbutton(opts, text="Dry run", variable=self.dry_run_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts, text="Test entry", variable=self.test_entry_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts, text="No OCO", variable=self.no_oco_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts, text="Shared magic", variable=self.shared_magic_var).pack(side="left", padx=(0, 12))

        # ---- Buttons
        btns = ttk.Frame(root)
        btns.pack(fill="x", expand=False, pady=(10, 10))
        ttk.Button(btns, text="Start", command=self.start).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Stop", command=self.stop).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Copy command", command=self.copy_command).pack(side="left")

        # ---- Logs
        out_frame = ttk.LabelFrame(root, text="Logs")
        out_frame.pack(fill="both", expand=True)

        self.text = tk.Text(out_frame, wrap="word")
        self.text.pack(side="left", fill="both", expand=True)

        out_scroll = ttk.Scrollbar(out_frame, orient="vertical", command=self.text.yview)
        out_scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=out_scroll.set)

        self._log("Ready. Make sure MT5 is configured in .env (repo root).\n")

    def selected_symbols(self) -> List[str]:
        sel = [self.symbol_list.get(i) for i in self.symbol_list.curselection()]
        custom = _tokens(self.custom_symbols_var.get())
        out = []
        for s in sel + custom:
            if s and s not in out:
                out.append(s)
        return out

    def build_command(self) -> List[str]:
        symbols = self.selected_symbols()
        if not symbols:
            symbols = ["EURUSD"]

        cmd: List[str] = [sys.executable, "-u", STRATEGY_PATH, "--symbols"] + symbols

        def add(name: str, value: str) -> None:
            v = str(value).strip()
            if v:
                cmd.extend([name, v])

        add("--impact", self.impact_var.get())
        add("--risk-pct", self.risk_pct_var.get())
        add("--sl-pips", self.sl_pips_var.get())
        add("--tp-pips", self.tp_pips_var.get())
        add("--risk-base", self.risk_base_var.get())
        add("--window-hours", self.window_hours_var.get())
        add("--lead-seconds", self.lead_seconds_var.get())
        add("--lookback-minutes", self.lookback_minutes_var.get())
        add("--distance-mult", self.distance_mult_var.get())
        add("--min-distance-pips", self.min_distance_pips_var.get())
        add("--lot", self.lot_var.get())
        add("--magic", self.magic_var.get())
        add("--poll-seconds", self.poll_seconds_var.get())
        add("--cancel-pending-after-minutes", self.cancel_pending_after_minutes_var.get())
        add("--close-position-after-minutes", self.close_position_after_minutes_var.get())

        cur_override = _tokens(self.currencies_override_var.get())
        if cur_override:
            cmd.append("--currencies")
            cmd.extend(cur_override)

        if self.dry_run_var.get():
            cmd.append("--dry-run")
        if self.test_entry_var.get():
            cmd.append("--test-entry")
            add("--test-hold-seconds", self.test_hold_seconds_var.get())
        if self.no_oco_var.get():
            cmd.append("--no-oco")
        if self.shared_magic_var.get():
            cmd.append("--shared-magic")

        return cmd

    def copy_command(self) -> None:
        cmd = self.build_command()
        pretty = " ".join([f'"{c}"' if " " in c else c for c in cmd])
        self.clipboard_clear()
        self.clipboard_append(pretty)
        self._log(f"Copied command to clipboard:\n{pretty}\n")

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            self._log("Already running.\n")
            return

        cmd = self.build_command()
        pretty = " ".join([f'"{c}"' if " " in c else c for c in cmd])
        self._log(f"Starting:\n{pretty}\n\n")

        # Ensure subprocess runs from repo root so relative imports behave.
        self.proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def reader() -> None:
            assert self.proc is not None
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self._log(line)
            rc = self.proc.poll()
            self._log(f"\nProcess exited with code {rc}\n")

        self.reader_thread = threading.Thread(target=reader, daemon=True)
        self.reader_thread.start()

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            self._log("Not running.\n")
            return
        self._log("Stopping process...\n")
        try:
            self.proc.terminate()
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        def append() -> None:
            self.text.insert("end", msg)
            self.text.see("end")

        self.after(0, append)


if __name__ == "__main__":
    app = StrategyCGui()
    app.mainloop()
