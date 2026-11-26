"""Windows launcher for building an offline map art review collection."""
import os
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from sort_canvases import build_collection, main as cli_main


def map_folder(source):
    # Accept the map store itself, the overworld folder, or its backup root.
    candidates = (source, source / "minecraft/maps", source / "data",
                  source / "data/minecraft/maps", source / "world/data", source / "world/data/minecraft/maps")
    found = [p for p in candidates if p.is_dir() and
             any(re.fullmatch(r"(?:map_)?-?[0-9]+\.dat", f.name, re.I) and f.is_file() for f in p.iterdir())]
    if not found:
        raise ValueError("No map DAT files found. Choose the folder containing map_25.dat or 25.dat.")
    if len(found) > 1:
        raise ValueError("This folder contains multiple map stores. Choose the specific data folder you want to sort.")
    return found[0]


def output_path(parent, zipped):
    name = "Map Art Review"
    suffix = ".zip" if zipped else ""
    path = parent / (name + suffix)
    n = 2
    while path.exists():
        path = parent / f"{name} ({n}){suffix}"
        n += 1
    return path


def launch():
    root = tk.Tk()
    root.title("Map Art Sorter")
    root.minsize(560, 360)
    root.columnconfigure(0, weight=1)
    body = ttk.Frame(root, padding=20)
    body.grid(sticky="nsew")
    body.columnconfigure(0, weight=1)
    ttk.Label(body, text="Build a map art gallery", font=("Segoe UI", 18, "bold")).grid(columnspan=2, sticky="w")
    source = tk.StringVar()
    destination = tk.StringVar()
    zipped = tk.BooleanVar()
    status = tk.StringVar(value="Choose your extracted Minecraft world and where to save the gallery.")
    controls = []

    def select_row(row, label, var):
        ttk.Label(body, text=label).grid(row=row, columnspan=2, sticky="w", pady=(12, 3))
        entry = ttk.Entry(body, textvariable=var, width=55)
        entry.grid(row=row+1, column=0, sticky="ew")
        def browse():
            path = filedialog.askdirectory(parent=root, title=label, mustexist=True)
            if path:
                var.set(path)
        pick = ttk.Button(body, text="Browse…", command=browse)
        pick.grid(row=row+1, column=1, padx=(8,0))
        controls.extend((entry, pick))

    select_row(1, "World or map data folder", source)
    select_row(3, "Save the gallery in", destination)
    reports = []
    report_status = tk.StringVar(value="No reports selected")
    ttk.Label(body, text="Map names (optional) · choose reports from the same save").grid(
        row=5, columnspan=2, sticky="w", pady=(12, 3))
    report_bar = ttk.Frame(body)
    report_bar.grid(row=6, columnspan=2, sticky="w")

    def add_reports():
        chosen = filedialog.askopenfilenames(parent=root, title="Add map-names.json or curated-map-names.csv",
                                             filetypes=[("Map name reports", "*.json *.csv")])
        for filename in chosen:
            path = Path(filename)
            if path not in reports:
                reports.append(path)
        if reports:
            report_status.set(f"{len(reports)} selected · latest: {reports[-1].parent.name}/{reports[-1].name}")

    def clear_reports():
        reports.clear()
        report_status.set("No reports selected")

    add = ttk.Button(report_bar, text="Add name reports…", command=add_reports)
    clear = ttk.Button(report_bar, text="Clear", command=clear_reports)
    add.pack(side="left")
    clear.pack(side="left", padx=6)
    ttk.Label(body, textvariable=report_status, wraplength=515).grid(row=7, columnspan=2, sticky="w")
    controls.extend((add, clear))
    share = ttk.Checkbutton(body, text="Create a ZIP to share with reviewers", variable=zipped)
    share.grid(row=8, columnspan=2, sticky="w", pady=10)
    controls.append(share)
    progress = ttk.Progressbar(body, mode="indeterminate")
    progress.grid(row=9, columnspan=2, sticky="ew")
    ttk.Label(body, textvariable=status, wraplength=515).grid(row=10, columnspan=2, sticky="w", pady=8)
    buttons = ttk.Frame(body)
    buttons.grid(row=11, columnspan=2, sticky="w")
    events = queue.Queue()
    busy = False
    result = None

    def start():
        nonlocal busy, result
        if not source.get().strip() or not destination.get().strip():
            messagebox.showinfo("Choose folders", "Choose both folders first.", parent=root)
            return
        parent = Path(destination.get().strip())
        if not parent.is_dir():
            messagebox.showerror("Output folder unavailable", "Choose an existing folder for the output.", parent=root)
            return
        src = Path(source.get().strip())
        out = output_path(parent, zipped.get())
        selected_reports = tuple(reports)
        def work():
            try:
                counts = build_collection(map_folder(src), out, names_reports=selected_reports)
                events.put((out, counts, None))
            except Exception as exc:
                events.put((None, None, str(exc)))
        busy, result = True, None
        for w in controls:
            w.state(["disabled"])
        view.state(["disabled"])
        status.set("Reading map files and building your gallery…")
        progress.start(15)
        threading.Thread(target=work).start()
        root.after(150, poll)

    def poll():
        nonlocal busy, result
        try:
            result, counts, error = events.get_nowait()
        except queue.Empty:
            root.after(150, poll)
            return
        busy = False
        progress.stop()
        for w in controls:
            w.state(["!disabled"])
        if error is not None:
            status.set("The gallery could not be built.")
            messagebox.showerror("Map Art Sorter", error, parent=root)
            return
        n, sites, issues = counts
        status.set(f"{n:,} map IDs across {sites:,} footprints. Saved to {result}." +
                   (f" See map-index.csv for {issues} issue(s)." if issues else ""))
        view.configure(text="Open output folder" if result.suffix == ".zip" else "Open review gallery")
        view.state(["!disabled"])

    def open_result():
        if result:
            try:
                os.startfile(result.parent if result.suffix == ".zip" else result / "review.html")
            except OSError as exc:
                messagebox.showerror("Unable to open result", str(exc), parent=root)

    def close():
        if busy:
            messagebox.showinfo("Sort in progress", "Wait for the gallery to finish before closing the sorter.", parent=root)
        else:
            root.destroy()

    build = ttk.Button(buttons, text="Build gallery", command=start)
    build.pack(side="left")
    controls.append(build)
    view = ttk.Button(buttons, text="Open review gallery", command=open_result, state="disabled")
    view.pack(side="left", padx=8)
    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(cli_main())
    launch()
