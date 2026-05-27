from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .core import (
    PrinterError,
    ProbeResult,
    UsbPrinterInfo,
    build_reset_preview,
    probe_printer,
    reset_ink_pad_counter,
    scan_epson_usb_printers,
)


CARD = "#111822"
CARD_DARK = "#0f1722"
CARD_INNER = "#101a28"
BORDER = "#15263a"
TEXT = "#d7e2ee"
MUTED = "#8ea6bf"
BLUE = "#1f5fa8"
BLUE_HOVER = "#174f8d"
GREEN = "#1a914e"
GREEN_HOVER = "#14753f"
RED = "#b64242"
RED_HOVER = "#933636"
ORANGE = "#7c5a22"
ORANGE_HOVER = "#66481b"
APP_DIR = Path(__file__).resolve().parents[1]
BACKUP_DIR = APP_DIR / "runtime" / "backups"


@dataclass(frozen=True)
class JobResult:
    action: str
    payload: object = None
    error: str = ""


class SelectionList(ctk.CTkFrame):
    def __init__(self, master, *, height: int, empty_label: str, on_change: Callable[[str], None] | None = None) -> None:
        super().__init__(master, fg_color="#121a25", corner_radius=10, border_width=1, border_color="#17283b")
        self.grid_columnconfigure(0, weight=1)
        self._on_change = on_change
        self._empty_label = empty_label
        self._selected_value = ""
        self._buttons: dict[str, ctk.CTkButton] = {}

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", height=height)
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.set_values([])

    def set_values(self, values: list[str], *, selected: str | None = None) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self._buttons.clear()
        values = [value for value in values if value]
        if not values:
            self._selected_value = ""
            ctk.CTkLabel(self.list_frame, text=self._empty_label, text_color=MUTED).grid(
                row=0, column=0, sticky="ew", padx=6, pady=6
            )
            return

        self._selected_value = selected if selected in values else values[0]
        for row, value in enumerate(values):
            button = ctk.CTkButton(
                self.list_frame,
                text=value,
                anchor="w",
                height=34,
                font=ctk.CTkFont("Segoe UI", 12),
                fg_color="transparent",
                hover_color="#17212d",
                command=lambda item=value: self.set(item),
            )
            button.grid(row=row, column=0, sticky="ew", padx=6, pady=3)
            self._buttons[value] = button
        self._refresh_styles()

    def _refresh_styles(self) -> None:
        for value, button in self._buttons.items():
            selected = value == self._selected_value
            button.configure(
                fg_color=BLUE if selected else "transparent",
                hover_color=BLUE_HOVER if selected else "#17212d",
                text_color="#f5fbff" if selected else "#b8c7d9",
            )

    def set(self, value: str, *, emit: bool = True) -> None:
        if value not in self._buttons:
            return
        self._selected_value = value
        self._refresh_styles()
        if emit and self._on_change is not None:
            self._on_change(value)

    def get(self) -> str:
        return self._selected_value


class KeyValuePanel(ctk.CTkFrame):
    def __init__(self, master, title: str) -> None:
        super().__init__(master, fg_color=CARD_INNER, corner_radius=12)
        self.grid_columnconfigure(0, weight=1)
        self._row = 1
        ctk.CTkLabel(self, text=title, text_color=TEXT, font=ctk.CTkFont("Segoe UI", 15, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 6)
        )

    def clear(self) -> None:
        for child in self.winfo_children()[1:]:
            child.destroy()
        self._row = 1

    def add(self, label: str, value: str, *, color: str = TEXT) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=self._row, column=0, sticky="ew", padx=12, pady=3)
        row.grid_columnconfigure(0, weight=0, minsize=108)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label, text_color=MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text=value, text_color=color, anchor="e", justify="right").grid(row=0, column=1, sticky="e")
        self._row += 1


class PrinterUtilityApp(ctk.CTk):
    POLL_MS = 120

    def __init__(self) -> None:
        super().__init__()
        self.title("INKPAD UTILITY")
        self.geometry("1040x700")
        self.minsize(940, 640)
        self.configure(fg_color="#0f131a")

        self._queue: queue.Queue[JobResult] = queue.Queue()
        self._devices: list[UsbPrinterInfo] = []
        self._selected_key = ""
        self._probe: ProbeResult | None = None
        self._busy = False
        self._active_tab = "Summary"
        self._tab_buttons: dict[str, ctk.CTkButton] = {}
        self._tab_frames: dict[str, ctk.CTkFrame] = {}
        self._reset_monitor_active = False
        self._reset_dialog: ctk.CTkToplevel | None = None
        self._reset_dialog_title: ctk.CTkLabel | None = None
        self._reset_dialog_body: ctk.CTkLabel | None = None
        self._reset_dialog_detail: ctk.CTkLabel | None = None
        self._reset_dialog_button: ctk.CTkButton | None = None

        self._build_ui()
        self.after(self.POLL_MS, self._poll_jobs)
        self._start_job("scan", scan_epson_usb_printers)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        shell.grid_columnconfigure(0, weight=28, minsize=260)
        shell.grid_columnconfigure(1, weight=38, minsize=350)
        shell.grid_columnconfigure(2, weight=34, minsize=280)
        shell.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(shell, fg_color="#101b2a", corner_radius=14)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="INKPAD UTILITY",
            font=ctk.CTkFont("Segoe UI", 28, "bold"),
            text_color="#4be08a",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        self.header_status = ctk.CTkLabel(
            header,
            text="USB service interface locked to read-only mode",
            text_color=MUTED,
            font=ctk.CTkFont("Segoe UI", 12),
        )
        self.header_status.grid(row=1, column=0, sticky="w", padx=18, pady=(2, 12))

        left = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        middle = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        right = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        middle.grid(row=1, column=1, sticky="nsew", padx=6)
        right.grid(row=1, column=2, sticky="nsew", padx=(6, 0))

        self._build_left(left)
        self._build_middle(middle)
        self._build_right(right)

    def _build_left(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        actions = ctk.CTkFrame(parent, fg_color=CARD_DARK, corner_radius=12)
        actions.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 10))
        actions.grid_columnconfigure(0, weight=1)

        self.scan_button = ctk.CTkButton(
            actions,
            text="SCAN USB",
            command=lambda: self._start_job("scan", scan_epson_usb_printers),
            height=46,
            fg_color=GREEN,
            hover_color=GREEN_HOVER,
            font=ctk.CTkFont("Segoe UI", 15, "bold"),
        )
        self.scan_button.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))

        self.read_button = ctk.CTkButton(
            actions,
            text="READ PRINTER",
            command=self._read_selected,
            height=40,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
        )
        self.read_button.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.reset_button = ctk.CTkButton(
            actions,
            text="RESET INFO",
            command=self._reset_pad_counter,
            height=40,
            fg_color=ORANGE,
            hover_color=ORANGE_HOVER,
        )
        self.reset_button.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))

        devices = ctk.CTkFrame(parent, fg_color=CARD_DARK, corner_radius=12)
        devices.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        devices.grid_columnconfigure(0, weight=1)
        devices.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(devices, text="Devices", text_color=TEXT, font=ctk.CTkFont("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 8)
        )
        self.device_note = ctk.CTkLabel(devices, text="Scanning...", text_color=MUTED, anchor="w")
        self.device_note.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))

        self.device_list = SelectionList(devices, height=260, empty_label="No Epson USB printer found", on_change=self._select_device)
        self.device_list.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        footer = ctk.CTkFrame(parent, fg_color=CARD_DARK, corner_radius=12)
        footer.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(footer, text="Session", text_color=TEXT, font=ctk.CTkFont("Segoe UI", 15, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 2)
        )
        self.action_note = ctk.CTkLabel(footer, text="Idle", text_color="#f0b15e", anchor="w", wraplength=220, justify="left")
        self.action_note.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _build_middle(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        self.device_panel = KeyValuePanel(parent, "Device")
        self.device_panel.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 10))

        self.status_panel = KeyValuePanel(parent, "Status")
        self.status_panel.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        log_card = ctk.CTkFrame(parent, fg_color=CARD_DARK, corner_radius=12)
        log_card.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(log_card, text="Log", text_color=TEXT, font=ctk.CTkFont("Segoe UI", 15, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 6)
        )
        self.log_box = ctk.CTkTextbox(
            log_card,
            fg_color="#0b1119",
            text_color="#b8c7d9",
            border_width=1,
            border_color="#17283b",
            corner_radius=8,
            wrap="word",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log_box.configure(state="disabled")

    def _build_right(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(parent, text="Ink Pad Service", text_color=TEXT, font=ctk.CTkFont("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 8)
        )
        tabs = ctk.CTkFrame(parent, fg_color="transparent")
        tabs.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        tabs.grid_columnconfigure((0, 1, 2), weight=1)

        for idx, name in enumerate(("Summary", "Bytes", "Raw")):
            button = ctk.CTkButton(
                tabs,
                text=name,
                height=36,
                fg_color="#223248",
                hover_color="#2a3c54",
                command=lambda tab=name: self._set_tab(tab),
            )
            button.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 4, 0 if idx == 2 else 4))
            self._tab_buttons[name] = button

        host = ctk.CTkFrame(parent, fg_color=CARD_DARK, corner_radius=12)
        host.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        host.grid_columnconfigure(0, weight=1)
        host.grid_rowconfigure(0, weight=1)

        self.summary_frame = self._make_scroll_frame(host)
        self.counter_frame = self._make_scroll_frame(host)
        self.raw_frame = self._make_scroll_frame(host)
        self._tab_frames = {
            "Summary": self.summary_frame,
            "Bytes": self.counter_frame,
            "Raw": self.raw_frame,
        }
        for frame in self._tab_frames.values():
            frame.grid(row=0, column=0, sticky="nsew")
        self._set_tab("Summary")
        self._refresh_tables()

    def _make_scroll_frame(self, parent) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        return frame

    def _set_tab(self, name: str) -> None:
        self._active_tab = name
        for tab_name, frame in self._tab_frames.items():
            if tab_name == name:
                frame.grid()
            else:
                frame.grid_remove()
        for tab_name, button in self._tab_buttons.items():
            button.configure(
                fg_color=BLUE if tab_name == name else "#223248",
                hover_color=BLUE_HOVER if tab_name == name else "#2a3c54",
            )

    def _start_job(self, action: str, callback: Callable[[], object]) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_buttons_busy(True)
        self._set_action(f"{action.title()} running...", "#7fc8ff")

        def worker() -> None:
            try:
                payload = callback()
                self._queue.put(JobResult(action=action, payload=payload))
            except Exception as exc:
                self._queue.put(JobResult(action=action, error=str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_jobs(self) -> None:
        try:
            while True:
                result = self._queue.get_nowait()
                self._handle_job_result(result)
        except queue.Empty:
            pass
        self.after(self.POLL_MS, self._poll_jobs)

    def _handle_job_result(self, result: JobResult) -> None:
        if result.action in {"reset_monitor_update", "reset_monitor_done"}:
            self._handle_reset_monitor_event(result)
            return

        self._busy = False
        self._set_buttons_busy(False)
        if result.error:
            self._set_action(result.error, "#ff8a8a")
            self._log(f"{result.action}: {result.error}")
            return
        if result.action == "scan":
            self._devices = list(result.payload or [])
            self._refresh_devices()
            self._set_action(f"Found {len(self._devices)} Epson USB device(s)", "#7ddd92" if self._devices else "#ffb26b")
            return
        if result.action == "read":
            self._probe = result.payload if isinstance(result.payload, ProbeResult) else None
            self._refresh_probe()
            self._set_action("Printer read complete", "#7ddd92")
            return
        if result.action == "reset":
            self._handle_reset_result(result.payload)
            return

    def _set_buttons_busy(self, busy: bool) -> None:
        if self._reset_monitor_active:
            self.scan_button.configure(state="disabled")
            self.read_button.configure(state="disabled")
            self.reset_button.configure(state="disabled")
            return
        state = "disabled" if busy else "normal"
        self.scan_button.configure(state=state)
        self.read_button.configure(state=state if self._devices else "disabled")
        self.reset_button.configure(state=state)

    def _refresh_devices(self) -> None:
        labels = [self._device_key(device) for device in self._devices]
        selected = self._selected_key if self._selected_key in labels else (labels[0] if labels else None)
        self.device_list.set_values(labels, selected=selected)
        self._selected_key = self.device_list.get()
        self.device_note.configure(text=f"{len(labels)} USB Epson device(s)")
        self.read_button.configure(state="normal" if labels and not self._busy else "disabled")
        self._refresh_selected_device()

    def _select_device(self, key: str) -> None:
        self._selected_key = key
        self._probe = None
        self._refresh_selected_device()
        self._refresh_probe()

    def _device_key(self, device: UsbPrinterInfo) -> str:
        service = device.service_interface
        suffix = f"if{service.number}" if service else "no service"
        return f"{device.display_name}  {suffix}"

    def _selected_device(self) -> UsbPrinterInfo | None:
        for device in self._devices:
            if self._device_key(device) == self._selected_key:
                return device
        return None

    def _refresh_selected_device(self) -> None:
        device = self._selected_device()
        self.device_panel.clear()
        if device is None:
            self.device_panel.add("Model", "none", color=MUTED)
            return
        service = device.service_interface
        self.device_panel.add("Model", device.product or "-")
        self.device_panel.add("Vendor", f"{device.vendor_id:04X}:{device.product_id:04X}")
        self.device_panel.add("Serial", device.serial or "-")
        self.device_panel.add("USB", f"bus {device.bus or '-'} / address {device.address or '-'}")
        self.device_panel.add("Service", f"interface {service.number}" if service else "not found", color="#7ddd92" if service else "#ff8a8a")

    def _read_selected(self) -> None:
        device = self._selected_device()
        if device is None:
            self._set_action("Select a printer first", "#ffb26b")
            return
        self._start_job("read", lambda: probe_printer(device))

    def _refresh_probe(self) -> None:
        self.status_panel.clear()
        if self._probe is None:
            self.status_panel.add("Read", "not started", color=MUTED)
            self._refresh_tables()
            return

        status = self._probe.status
        lock_text, lock_color = self._printer_state_label()
        self.status_panel.add("Read", "OK", color="#7ddd92")
        self.status_panel.add("Printer", lock_text, color=lock_color)
        self.status_panel.add("Message", status.error_text or status.status_text or "-", color=lock_color)
        self.status_panel.add("Model", str(self._probe.device_id.get("MDL") or self._probe.printer.product or "-"))
        self.status_panel.add("Serial", status.serial_info or str(self._probe.device_id.get("SN", "-")))
        commands = self._probe.device_id.get("CMD", ())
        self.status_panel.add("Protocol", ", ".join(commands) if isinstance(commands, tuple) else str(commands or "-"))
        self._log(f"Read {len(self._probe.counters)} waste-counter byte(s).")
        self._refresh_reset_button()
        self._refresh_tables()

    def _refresh_reset_button(self) -> None:
        if self._busy:
            return
        if self._probe is not None and self._probe.status.error_code == 0x10:
            self.reset_button.configure(
                text="RESET PAD COUNTER",
                fg_color=RED,
                hover_color=RED_HOVER,
                state="normal",
            )
            return
        self.reset_button.configure(
            text="RESET INFO",
            fg_color=ORANGE,
            hover_color=ORANGE_HOVER,
            state="normal",
        )

    def _printer_state_label(self) -> tuple[str, str]:
        if self._probe is None:
            return ("Unknown", MUTED)
        status = self._probe.status
        if status.error_code == 0x10:
            return ("Waste ink counter lock", "#ffb26b")
        if status.error_text:
            return ("Printer reports an error", "#ff8a8a")
        if status.ready:
            return ("Ready", "#7ddd92")
        return (status.status_text or "Unknown", "#ffb26b")

    def _refresh_tables(self) -> None:
        for frame in (self.summary_frame, self.counter_frame, self.raw_frame):
            for child in frame.winfo_children():
                child.destroy()
        if self._probe is None:
            self._placeholder(self.summary_frame, "Read the printer to show waste-counter status")
            self._placeholder(self.counter_frame, "No technical byte data")
            self._placeholder(self.raw_frame, "No raw status")
            return

        self._populate_summary()
        self._populate_counters()
        self._populate_raw()

    def _placeholder(self, frame: ctk.CTkScrollableFrame, text: str) -> None:
        ctk.CTkLabel(frame, text=text, text_color=MUTED).grid(row=0, column=0, sticky="ew", padx=10, pady=10)

    def _populate_summary(self) -> None:
        assert self._probe is not None
        lock_text, lock_color = self._printer_state_label()
        preview = build_reset_preview(self._probe.counters)
        readable = sum(1 for _address, current, _target in preview if current is not None)
        changes = sum(1 for _address, current, target in preview if current is not None and current != target)

        card = ctk.CTkFrame(self.summary_frame, fg_color=CARD_INNER, corner_radius=12)
        card.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 10))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            card,
            text=lock_text,
            text_color=lock_color,
            font=ctk.CTkFont("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        ctk.CTkLabel(
            card,
            text=self._probe.status.error_text or "Printer status was read successfully.",
            text_color=TEXT,
            wraplength=280,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 14))

        self._summary_metric(1, "Waste-counter bytes read", f"{readable}/{len(preview)}")
        self._summary_metric(2, "Bytes that reset would change", str(changes))
        self._summary_metric(3, "Reset action", "available after confirmation", color="#ffb26b")
        self._summary_metric(4, "After reset", "power-cycle the printer", color="#7fc8ff")

    def _summary_metric(self, row: int, label: str, value: str, *, color: str = TEXT) -> None:
        item = ctk.CTkFrame(self.summary_frame, fg_color="#101a28" if row % 2 else "transparent", corner_radius=8)
        item.grid(row=row, column=0, sticky="ew", padx=8, pady=3)
        item.grid_columnconfigure(0, weight=1)
        item.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(item, text=label, text_color=MUTED).grid(row=0, column=0, sticky="w", padx=10, pady=8)
        ctk.CTkLabel(item, text=value, text_color=color, font=ctk.CTkFont("Segoe UI", 12, "bold")).grid(
            row=0, column=1, sticky="e", padx=10, pady=8
        )

    def _populate_counters(self) -> None:
        self._table_header(self.counter_frame, ("Memory byte", "Read value", "Reset value"))
        for row, (address, current, target) in enumerate(build_reset_preview(self._probe.counters if self._probe else {}), start=1):
            self._table_row(
                self.counter_frame,
                row,
                (f"0x{address:03X}", "-" if current is None else f"0x{current:02X}", f"0x{target:02X}"),
                color="#ffb26b" if current is None else TEXT,
                columns=3,
            )
        note = ctk.CTkLabel(
            self.counter_frame,
            text="TECHNICAL VIEW - RESET BUTTON WRITES THESE VALUES",
            text_color="#ffb26b",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
        )
        note.grid(row=1000, column=0, sticky="ew", padx=10, pady=(12, 8))

    def _populate_raw(self) -> None:
        text = ctk.CTkTextbox(
            self.raw_frame,
            fg_color="#0b1119",
            text_color="#b8c7d9",
            border_width=1,
            border_color="#17283b",
            corner_radius=8,
            height=420,
            wrap="word",
        )
        text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        raw = self._probe.status.raw_hex if self._probe else ""
        text.insert("1.0", raw)
        text.configure(state="disabled")

    def _table_header(self, frame: ctk.CTkScrollableFrame, labels: tuple[str, ...]) -> None:
        self._table_row(frame, 0, labels, color="#7fc8ff", columns=len(labels), bold=True)

    def _table_row(
        self,
        frame: ctk.CTkScrollableFrame,
        row: int,
        values: tuple[str, ...],
        *,
        color: str = TEXT,
        columns: int = 2,
        bold: bool = False,
    ) -> None:
        line = ctk.CTkFrame(frame, fg_color="#101a28" if row % 2 else "transparent", corner_radius=8)
        line.grid(row=row, column=0, sticky="ew", padx=8, pady=2)
        for index in range(columns):
            line.grid_columnconfigure(index, weight=1)
            ctk.CTkLabel(
                line,
                text=values[index] if index < len(values) else "",
                text_color=color,
                font=ctk.CTkFont("Segoe UI", 12, "bold" if bold else "normal"),
            ).grid(row=0, column=index, sticky="w", padx=8, pady=5)

    def _reset_pad_counter(self) -> None:
        device = self._selected_device()
        if device is None:
            messagebox.showinfo("Read printer first", "Select the ET-2820 and read the printer before resetting.")
            return
        if self._probe is None:
            messagebox.showinfo("Read printer first", "Click READ PRINTER before resetting the pad counter.")
            return
        if self._probe.status.error_code != 0x10:
            messagebox.showinfo(
                "Reset not needed",
                "The printer is not currently reporting the waste ink pad counter overflow lock.",
            )
            return
        changes = sum(
            1
            for _address, current, target in build_reset_preview(self._probe.counters)
            if current is not None and current != target
        )
        confirmed = messagebox.askyesno(
            "Reset ink pad counter",
            "This will write the ET-2820 ink pad counter reset bytes to the printer.\n\n"
            f"Bytes to change: {changes}\n\n"
            "The app will read a backup first, write the reset values, verify them, then you must power the printer off and on.\n\n"
            "Continue?",
        )
        if not confirmed:
            self._set_action("Reset cancelled", "#ffb26b")
            return
        self._start_job("reset", lambda: reset_ink_pad_counter(device))

    def _handle_reset_result(self, payload: object) -> None:
        backup_path = self._save_reset_backup(payload)
        changed = len(getattr(payload, "changed", ()))
        serial = str(getattr(getattr(payload, "printer", None), "serial", "") or "")
        model = str(getattr(payload, "device_id", {}).get("MDL", "ET-2820 Series"))
        self._set_action("Reset verified. Waiting for power-cycle.", "#7ddd92")
        self._log(f"Reset verified. Changed {changed} byte(s). Backup: {backup_path.name}")
        self._show_reset_monitor_dialog(model=model, serial=serial, backup_path=backup_path)
        self._start_reset_monitor(serial=serial, backup_path=backup_path)

    def _show_reset_monitor_dialog(self, *, model: str, serial: str, backup_path: Path) -> None:
        if self._reset_dialog is not None and self._reset_dialog.winfo_exists():
            self._reset_dialog.destroy()

        dialog = ctk.CTkToplevel(self)
        dialog.title("Power-cycle printer")
        dialog.geometry("520x300")
        dialog.transient(self)
        dialog.configure(fg_color="#101821")
        dialog.grid_columnconfigure(0, weight=1)
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        self._reset_dialog = dialog
        self._reset_dialog_title = ctk.CTkLabel(
            dialog,
            text="Turn the printer off now",
            text_color="#ffb26b",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
        )
        self._reset_dialog_title.grid(row=0, column=0, sticky="w", padx=18, pady=(18, 8))

        self._reset_dialog_body = ctk.CTkLabel(
            dialog,
            text="Keep it off for 10 seconds. I am watching the USB list.",
            text_color=TEXT,
            wraplength=460,
            justify="left",
        )
        self._reset_dialog_body.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        self._reset_dialog_detail = ctk.CTkLabel(
            dialog,
            text=f"{model}\nSerial: {serial or '-'}\nBackup kept until the follow-up read passes: {backup_path.name}",
            text_color=MUTED,
            wraplength=460,
            justify="left",
        )
        self._reset_dialog_detail.grid(row=2, column=0, sticky="w", padx=18, pady=(0, 16))

        self._reset_dialog_button = ctk.CTkButton(
            dialog,
            text="WAITING FOR PRINTER",
            state="disabled",
            height=40,
            fg_color="#223248",
            hover_color="#2a3c54",
            command=self._finish_reset_workflow,
        )
        self._reset_dialog_button.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        dialog.lift()
        dialog.focus_force()

    def _start_reset_monitor(self, *, serial: str, backup_path: Path) -> None:
        self._reset_monitor_active = True
        self._set_buttons_busy(False)

        def monitor() -> None:
            phase = "wait_off"
            off_seen_at: float | None = None
            deadline = time.monotonic() + 240
            last_message = ""

            def send_update(title: str, body: str, detail: str, color: str = "#ffb26b") -> None:
                nonlocal last_message
                message = f"{title}|{body}|{detail}"
                if message == last_message:
                    return
                last_message = message
                self._queue.put(
                    JobResult(
                        action="reset_monitor_update",
                        payload={"title": title, "body": body, "detail": detail, "color": color},
                    )
                )

            while time.monotonic() < deadline:
                try:
                    devices = scan_epson_usb_printers()
                except Exception as exc:
                    send_update(
                        "Watching USB",
                        "The printer may be switching USB states.",
                        f"Last scan error: {exc}",
                    )
                    time.sleep(1.0)
                    continue

                matched = self._match_reset_device(devices, serial)
                if phase == "wait_off":
                    if matched is None:
                        phase = "wait_delay"
                        off_seen_at = time.monotonic()
                        send_update(
                            "Printer is off",
                            "Keep it off. I will ask you to start it again after 10 seconds.",
                            "USB device disappeared.",
                            "#7fc8ff",
                        )
                    else:
                        send_update(
                            "Turn the printer off now",
                            "I am waiting for the ET-2820 to disappear from USB.",
                            "Do not unplug USB; use the printer power button.",
                        )
                elif phase == "wait_delay":
                    elapsed = int(time.monotonic() - (off_seen_at or time.monotonic()))
                    remaining = max(0, 10 - elapsed)
                    if remaining > 0:
                        send_update(
                            "Printer is off",
                            f"Wait {remaining} more second(s).",
                            "This gives the printer controller time to fully restart.",
                            "#7fc8ff",
                        )
                    else:
                        phase = "wait_on"
                        send_update(
                            "Start the printer now",
                            "Turn the printer back on. I will detect it automatically.",
                            "Waiting for the USB device to reappear.",
                            "#7fc8ff",
                        )
                elif phase == "wait_on":
                    if matched is None:
                        send_update(
                            "Start the printer now",
                            "Waiting for the ET-2820 to reappear on USB.",
                            "This can take a few seconds after power-on.",
                            "#7fc8ff",
                        )
                    else:
                        send_update(
                            "Printer detected",
                            "Reading status after restart.",
                            "Checking whether the ink pad lock cleared.",
                            "#7fc8ff",
                        )
                        try:
                            probe = probe_printer(matched)
                        except Exception as exc:
                            self._queue.put(
                                JobResult(
                                    action="reset_monitor_done",
                                    payload={
                                        "success": False,
                                        "devices": devices,
                                        "probe": None,
                                        "backup_path": backup_path,
                                        "message": f"Printer returned, but follow-up read failed: {exc}",
                                    },
                                )
                            )
                            return
                        success = probe.status.error_code != 0x10
                        self._queue.put(
                            JobResult(
                                action="reset_monitor_done",
                                payload={
                                    "success": success,
                                    "devices": devices,
                                    "probe": probe,
                                    "backup_path": backup_path,
                                    "message": "Ink pad counter lock cleared."
                                    if success
                                    else "Printer still reports the ink pad counter lock.",
                                },
                            )
                        )
                        return
                time.sleep(1.0)

            self._queue.put(
                JobResult(
                    action="reset_monitor_done",
                    payload={
                        "success": False,
                        "devices": [],
                        "probe": None,
                        "backup_path": backup_path,
                        "message": "Timed out waiting for the printer power-cycle.",
                    },
                )
            )

        threading.Thread(target=monitor, daemon=True).start()

    def _match_reset_device(self, devices: list[UsbPrinterInfo], serial: str) -> UsbPrinterInfo | None:
        for device in devices:
            if serial and device.serial == serial:
                return device
        if len(devices) == 1:
            return devices[0]
        return None

    def _handle_reset_monitor_event(self, result: JobResult) -> None:
        payload = result.payload if isinstance(result.payload, dict) else {}
        if result.action == "reset_monitor_update":
            self._update_reset_dialog(
                title=str(payload.get("title", "")),
                body=str(payload.get("body", "")),
                detail=str(payload.get("detail", "")),
                color=str(payload.get("color", "#ffb26b")),
                done=False,
            )
            return

        self._reset_monitor_active = False
        self._set_buttons_busy(False)
        devices = payload.get("devices")
        if isinstance(devices, list):
            self._devices = devices
            self._refresh_devices()
        probe = payload.get("probe")
        if isinstance(probe, ProbeResult):
            self._probe = probe
            self._selected_key = self._device_key(probe.printer)
            self._refresh_devices()
            self._refresh_probe()

        backup_path = payload.get("backup_path")
        success = bool(payload.get("success", False))
        message = str(payload.get("message", ""))
        backup_note = ""
        if success and isinstance(backup_path, Path):
            try:
                backup_path.unlink(missing_ok=True)
                backup_note = f"Backup deleted: {backup_path.name}"
            except Exception as exc:
                backup_note = f"Backup could not be deleted: {exc}"

        if success:
            self._set_action("Reset worked. Printer is ready for use.", "#7ddd92")
            self._log(f"Post-reset check passed. {backup_note}")
            self._update_reset_dialog(
                title="Reset worked",
                body="The printer came back and the ink pad counter lock is gone. Start your print again.",
                detail=backup_note or message,
                color="#7ddd92",
                done=True,
            )
        else:
            self._set_action("Post-reset check needs attention.", "#ffb26b")
            self._log(message)
            self._update_reset_dialog(
                title="Check did not pass",
                body=message,
                detail="The backup was kept so the pre-reset values are still available.",
                color="#ffb26b",
                done=True,
            )

    def _update_reset_dialog(self, *, title: str, body: str, detail: str, color: str, done: bool) -> None:
        if self._reset_dialog is None or not self._reset_dialog.winfo_exists():
            return
        if self._reset_dialog_title is not None:
            self._reset_dialog_title.configure(text=title, text_color=color)
        if self._reset_dialog_body is not None:
            self._reset_dialog_body.configure(text=body)
        if self._reset_dialog_detail is not None:
            self._reset_dialog_detail.configure(text=detail)
        if self._reset_dialog_button is not None and done:
            self._reset_dialog_button.configure(
                text="NEXT PRINTER / NEW SCAN",
                state="normal",
                fg_color=GREEN,
                hover_color=GREEN_HOVER,
            )

    def _finish_reset_workflow(self) -> None:
        if self._reset_dialog is not None and self._reset_dialog.winfo_exists():
            self._reset_dialog.destroy()
        self._reset_dialog = None
        self._reset_dialog_title = None
        self._reset_dialog_body = None
        self._reset_dialog_detail = None
        self._reset_dialog_button = None
        self._probe = None
        self._refresh_probe()
        self._start_job("scan", scan_epson_usb_printers)

    def _save_reset_backup(self, payload: object) -> Path:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        serial = getattr(payload, "device_id", {}).get("SN", "unknown")
        backup_path = BACKUP_DIR / f"reset_backup_{serial}_{stamp}.json"

        def encode_values(values: dict[int, int | None]) -> dict[str, str | None]:
            return {
                f"0x{address:03X}": None if value is None else f"0x{value:02X}"
                for address, value in values.items()
            }

        data = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "model": getattr(payload, "device_id", {}).get("MDL"),
            "serial": serial,
            "verified": bool(getattr(payload, "verified", False)),
            "changed": [f"0x{address:03X}" for address in getattr(payload, "changed", ())],
            "before": encode_values(getattr(payload, "before", {})),
            "target": encode_values(getattr(payload, "target", {})),
            "after": encode_values(getattr(payload, "after", {})),
        }
        backup_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return backup_path

    def _set_action(self, text: str, color: str) -> None:
        self.action_note.configure(text=text, text_color=color)
        self.header_status.configure(text=text)

    def _log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{text}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


def run() -> None:
    app = PrinterUtilityApp()
    app.mainloop()
