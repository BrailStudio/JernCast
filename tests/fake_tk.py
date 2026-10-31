"""A stand-in for tkinter, so the window's code can run where no screen exists.

It cannot show how the window looks. It only lets the tests press the window's
buttons and check what the program does.
"""
import sys
import types


class Var:
    def __init__(self, master=None, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, v):
        self.value = v

    def trace_add(self, *a):
        pass


class Widget:
    def __init__(self, *args, **kwargs):
        self.options = dict(kwargs)
        self.children = []
        self._state = []

    def __getitem__(self, key):
        return self.options.get(key)

    def __setitem__(self, key, value):
        self.options[key] = value

    def configure(self, **kw):
        self.options.update(kw)

    config = configure

    def state(self, spec=None):
        if spec is not None:
            for s in spec:
                if s.startswith("!"):
                    if s[1:] in self._state:
                        self._state.remove(s[1:])
                elif s not in self._state:
                    self._state.append(s)
        return list(self._state)

    def winfo_width(self):
        return 900

    def winfo_height(self):
        return 400

    def winfo_children(self):
        return []

    def wait_window(self, win):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


class Style(Widget):
    def configure(self, *a, **k):
        pass

    def map(self, *a, **k):
        pass

    def theme_use(self, *a):
        pass


class Listbox(Widget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.items, self.selected = [], []

    def delete(self, a, b=None):
        self.items = []
        self.selected = []

    def insert(self, where, text):
        self.items.append(text)

    def size(self):
        return len(self.items)

    def selection_clear(self, a, b=None):
        self.selected = []

    def selection_set(self, i):
        self.selected = [i]

    def curselection(self):
        return tuple(self.selected)


class Canvas(Widget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.drawn = []

    def delete(self, *a):
        self.drawn = []

    def create_rectangle(self, *a, **k):
        self.drawn.append(("rect", a))

    def create_text(self, *a, **k):
        self.drawn.append(("text", k.get("text")))


class Scale(Widget):
    def set(self, v):
        self.options["value"] = v

    def get(self):
        return self.options.get("value", 0)


def install():
    """Return the fake modules; use with mock.patch.dict(sys.modules, ...)."""
    tk = types.ModuleType("tkinter")
    tk.TclError = type("TclError", (Exception,), {})
    tk.StringVar = tk.BooleanVar = Var
    for name, cls in (("Tk", Widget), ("Toplevel", Widget), ("Label", Widget), ("Menu", Widget),
                      ("PhotoImage", Widget), ("Listbox", Listbox), ("Canvas", Canvas), ("Scale", Scale)):
        setattr(tk, name, cls)
    ttk = types.ModuleType("tkinter.ttk")
    ttk.Style = Style
    for name in ("Frame", "LabelFrame", "Label", "Button", "Radiobutton", "Checkbutton",
                 "Combobox", "Entry", "Menubutton", "Notebook"):
        setattr(ttk, name, Widget)
    log = []
    msgs = types.ModuleType("tkinter.messagebox")
    msgs.shown = log
    for name in ("showinfo", "showerror", "showwarning"):
        setattr(msgs, name, lambda *a, _n=name, **k: log.append((_n, a)))
    msgs.askyesno = lambda *a, **k: True
    files = types.ModuleType("tkinter.filedialog")
    files.askdirectory = files.askopenfilename = lambda *a, **k: ""
    simple = types.ModuleType("tkinter.simpledialog")
    simple.answers = []
    simple.askstring = lambda *a, **k: simple.answers.pop(0) if simple.answers else None
    tk.ttk, tk.messagebox, tk.filedialog, tk.simpledialog = ttk, msgs, files, simple
    return {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": msgs,
            "tkinter.filedialog": files, "tkinter.simpledialog": simple}
