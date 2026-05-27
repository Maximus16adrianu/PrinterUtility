from __future__ import annotations

import customtkinter as ctk

from .ui import run


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    ctk.set_widget_scaling(0.92)
    ctk.set_window_scaling(0.96)
    run()


if __name__ == "__main__":
    main()
