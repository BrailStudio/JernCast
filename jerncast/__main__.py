import logging
import sys
import traceback

from .core import data_dir
from .winutil import set_dpi_aware


def main() -> None:
    handlers = [logging.FileHandler(data_dir() / "jerncast.log", encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")
    set_dpi_aware()
    try:
        import tkinter as tk
        from .gui import App
        root = tk.Tk()
        App(root)
        root.mainloop()
    except Exception:
        logging.exception("JernCast crashed")
        try:
            from tkinter import messagebox
            messagebox.showerror("JernCast", "JernCast crashed:\n\n" + traceback.format_exc()[-1500:])
        except Exception:
            print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
