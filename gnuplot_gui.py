#!/usr/bin/env python3
"""
Filename: gnuplot_gui.py
Author: G. Vijaya Kumar
Date: Sep 5, 2025
Description: A GUI for gnuplot (built to monitor OpenFOAM simulations)

To run: python3 gnuplot_gui.py
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import time
from PIL import Image, ImageTk
import platform 
import os       

# --- Main Application Class ---
class GnuplotApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Embedded Gnuplot GUI V13.0") # Version bump!
        self.root.geometry("1200x800")
        
        self.auto_replotting = False
        self.active_auto_replot_job = None
        
        self.tabs = {}
        self.tab_counter = 0
        self.right_clicked_tab_id = None # Store which tab was right-clicked

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        # Create right-click menu for tabs and bind it
        self.tab_menu = tk.Menu(self.root, tearoff=0)
        self.tab_menu.add_command(label="Rename Tab", command=self.rename_tab_popup)
        self.notebook.bind("<Button-3>", self.show_tab_menu)

        # Start with one tab and the '+' tab
        self.tab_counter += 1
        first_title = f"Plot {self.tab_counter}"
        first_key = f"tab{self.tab_counter}"
        first_tab_frame = self.create_plot_tab(first_title, first_key)
        self.notebook.add(first_tab_frame, text=first_title)
        
        self.plus_tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.plus_tab_frame, text='+')
        
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def show_tab_menu(self, event):
        try:
            tab_index = event.widget.index(f"@{event.x},{event.y}")
            if event.widget.tab(tab_index, "text") == '+': return
            self.right_clicked_tab_id = event.widget.tabs()[tab_index]
            self.tab_menu.post(event.x_root, event.y_root)
        except tk.TclError: pass

    def rename_tab_popup(self):
        if not self.right_clicked_tab_id: return
        popup = tk.Toplevel(self.root)
        popup.title("Rename Tab")
        popup.transient(self.root); popup.grab_set()
        x, y = self.root.winfo_x() + 300, self.root.winfo_y() + 200
        popup.geometry(f"250x100+{x}+{y}")
        ttk.Label(popup, text="Enter new tab name:").pack(pady=10)
        new_name_var = tk.StringVar()
        entry = ttk.Entry(popup, textvariable=new_name_var)
        entry.pack(padx=10, fill='x'); entry.focus()
        entry.bind("<Return>", lambda e: on_ok())

        def on_ok():
            new_name = new_name_var.get().strip()
            if new_name: self.notebook.tab(self.right_clicked_tab_id, text=new_name)
            popup.destroy()

        button_frame = ttk.Frame(popup); button_frame.pack(pady=10)
        ttk.Button(button_frame, text="OK", command=on_ok).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Cancel", command=popup.destroy).pack(side='left', padx=5)

    def add_new_tab(self):
        self.tab_counter += 1
        title = f"Plot {self.tab_counter}"
        key = f"tab{self.tab_counter}"
        new_tab_frame = self.create_plot_tab(title, key)
        plus_tab_index = self.notebook.index('end') - 1
        self.notebook.insert(plus_tab_index, new_tab_frame, text=title)
        self.notebook.select(plus_tab_index)

    def close_tab(self, key):
        if len(self.notebook.tabs()) <= 2:
            messagebox.showwarning("Action Blocked", "Cannot close the last plot tab.")
            return
        frame_to_close = self.tabs[key]['frame']
        self.notebook.forget(frame_to_close)
        del self.tabs[key]

    def on_tab_changed(self, event):
        try:
            selected_tab_text = event.widget.tab(event.widget.select(), "text")
            if selected_tab_text == '+': self.add_new_tab()
        except tk.TclError: pass
            
    def create_plot_tab(self, title, key):
        tab_frame = ttk.Frame(self.notebook)
        paned_window = ttk.PanedWindow(tab_frame, orient='horizontal')
        paned_window.pack(expand=True, fill='both')
        controls_frame = ttk.Frame(paned_window, padding="10")
        paned_window.add(controls_frame, weight=1)
        plot_frame = ttk.Frame(paned_window, padding="10")
        paned_window.add(plot_frame, weight=2)
        widgets = {}
        
        dataset_frame = ttk.LabelFrame(controls_frame, text="Datasets", padding=10); dataset_frame.pack(fill='x', pady=5)
        columns = ("file", "x_col", "y_col", "axis", "style", "title", "clean")
        widgets['tree'] = ttk.Treeview(dataset_frame, columns=columns, show="tree headings", height=4)
        widgets['tree'].heading("#0", text="Show"); widgets['tree'].column("#0", width=40, anchor='center', stretch=False)
        widgets['tree'].heading("file", text="File"); widgets['tree'].heading("x_col", text="X"); widgets['tree'].heading("y_col", text="Y"); widgets['tree'].heading("axis", text="Axis"); widgets['tree'].heading("style", text="Style"); widgets['tree'].heading("title", text="Title"); widgets['tree'].heading("clean", text="Clean")
        widgets['tree'].column("file", width=100); widgets['tree'].column("x_col", width=30, anchor='center'); widgets['tree'].column("y_col", width=30, anchor='center'); widgets['tree'].column("axis", width=40, anchor='center'); widgets['tree'].column("style", width=60); widgets['tree'].column("clean", width=40, anchor='center'); widgets['tree'].pack(fill='x')
        widgets['tree'].bind("<<TreeviewSelect>>", lambda event, w=widgets: self.on_tree_select(event, w))
        widgets['tree'].bind("<Button-1>", lambda event, w=widgets, k=key: self.toggle_checkbox(event, w, k))

        editor_frame = ttk.LabelFrame(controls_frame, text="Dataset Editor", padding=10); editor_frame.pack(fill='x', pady=5)
        widgets['filepath'] = tk.StringVar(); filepath_entry = ttk.Entry(editor_frame, textvariable=widgets['filepath'], width=25); filepath_entry.grid(row=0, column=1, sticky="ew", columnspan=3); filepath_entry.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(editor_frame, text="Data File:").grid(row=0, column=0, sticky="w", pady=2); ttk.Button(editor_frame, text="Browse...", command=lambda w=widgets: self.browse_file(w)).grid(row=0, column=4, padx=5)
        widgets['x_col'] = tk.StringVar(value='1'); x_col_entry = ttk.Entry(editor_frame, textvariable=widgets['x_col'], width=5); x_col_entry.grid(row=1, column=1, sticky="w"); x_col_entry.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(editor_frame, text="X Col:").grid(row=1, column=0, sticky="w", pady=2)
        widgets['y_col'] = tk.StringVar(value='2'); y_col_entry = ttk.Entry(editor_frame, textvariable=widgets['y_col'], width=5); y_col_entry.grid(row=1, column=3, sticky="w"); y_col_entry.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(editor_frame, text="Y Col:").grid(row=1, column=2, sticky="e", pady=2, padx=5)
        widgets['y_axis_select'] = tk.StringVar(value='Y1'); ttk.Label(editor_frame, text="Axis:").grid(row=1, column=4, sticky="e", padx=(10,2)); ttk.Combobox(editor_frame, textvariable=widgets['y_axis_select'], values=['Y1', 'Y2'], width=4).grid(row=1, column=5, sticky="w")
        widgets['plot_style'] = tk.StringVar(value='lines'); ttk.Label(editor_frame, text="Plot Style:").grid(row=2, column=0, sticky="w", pady=2); ttk.Combobox(editor_frame, textvariable=widgets['plot_style'], values=['lines', 'points', 'linespoints', 'dots', 'impulses'], width=15).grid(row=2, column=1, sticky="ew", columnspan=2)
        widgets['plot_title'] = tk.StringVar(); plot_title_entry = ttk.Entry(editor_frame, textvariable=widgets['plot_title'], width=20); plot_title_entry.grid(row=3, column=1, sticky="ew", columnspan=3); plot_title_entry.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(editor_frame, text="Title:").grid(row=3, column=0, sticky="w", pady=2)
        
        options_frame = ttk.Frame(editor_frame); options_frame.grid(row=4, column=0, columnspan=6, sticky='w', pady=5)
        widgets['clean_data'] = tk.BooleanVar(value=False)
        widgets['detect_headers'] = tk.BooleanVar(value=True)
        
        clean_cb = ttk.Checkbutton(options_frame, text="Clean Vector Data ( )", variable=widgets['clean_data'], command=lambda w=widgets: self._on_clean_data_toggle(w))
        clean_cb.pack(side='left')
        
        detect_headers_cb = ttk.Checkbutton(options_frame, text="Detect Column Headers", variable=widgets['detect_headers'])
        detect_headers_cb.pack(side='left', padx=10)
        widgets['detect_headers_cb'] = detect_headers_cb

        dataset_actions_frame = ttk.Frame(controls_frame); dataset_actions_frame.pack(fill='x', pady=5)
        ttk.Button(dataset_actions_frame, text="Add Dataset", command=lambda w=widgets, k=key: self.add_dataset(w, k)).pack(side='left', padx=5)
        widgets['update_button'] = ttk.Button(dataset_actions_frame, text="Update Selected", state="disabled", command=lambda w=widgets, k=key: self.update_dataset(w, k)); widgets['update_button'].pack(side='left', padx=5)
        widgets['duplicate_button'] = ttk.Button(dataset_actions_frame, text="Duplicate Selected", state="disabled", command=lambda w=widgets, k=key: self.duplicate_dataset(w, k)); widgets['duplicate_button'].pack(side='left', padx=5)
        widgets['remove_button'] = ttk.Button(dataset_actions_frame, text="Remove Selected", state="disabled", command=lambda w=widgets, k=key: self.remove_dataset(w, k)); widgets['remove_button'].pack(side='left', padx=5)

        axis_frame = ttk.LabelFrame(controls_frame, text="Axes Settings", padding=10); axis_frame.pack(fill='x', pady=5)
        widgets['xlabel'] = tk.StringVar(); xlabel_entry = ttk.Entry(axis_frame, textvariable=widgets['xlabel'], width=30); xlabel_entry.grid(row=0, column=1, columnspan=5, sticky="ew"); xlabel_entry.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(axis_frame, text="X-Axis Title:").grid(row=0, column=0, sticky="w", pady=2)
        widgets['ylabel'] = tk.StringVar(); ylabel_entry = ttk.Entry(axis_frame, textvariable=widgets['ylabel'], width=30); ylabel_entry.grid(row=1, column=1, columnspan=5, sticky="ew"); ylabel_entry.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(axis_frame, text="Y1-Axis Title:").grid(row=1, column=0, sticky="w", pady=2)
        widgets['y2label'] = tk.StringVar(); y2label_entry = ttk.Entry(axis_frame, textvariable=widgets['y2label'], width=30); y2label_entry.grid(row=2, column=1, columnspan=5, sticky="ew"); y2label_entry.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(axis_frame, text="Y2-Axis Title:").grid(row=2, column=0, sticky="w", pady=2)
        widgets['x_log'] = tk.BooleanVar(); widgets['y_log'] = tk.BooleanVar(); widgets['y2_log'] = tk.BooleanVar(); widgets['grid_on'] = tk.BooleanVar(value=True); widgets['grid_style'] = tk.StringVar(value='Medium')
        
        grid_frame = ttk.Frame(axis_frame); grid_frame.grid(row=3, column=0, columnspan=6, sticky='w', pady=5)
        ttk.Checkbutton(grid_frame, text="X Log", variable=widgets['x_log'], command=lambda w=widgets, k=key: self.plot(w, k)).pack(side='left', padx=(0,10))
        ttk.Checkbutton(grid_frame, text="Y1 Log", variable=widgets['y_log'], command=lambda w=widgets, k=key: self.plot(w, k)).pack(side='left', padx=(0,10))
        ttk.Checkbutton(grid_frame, text="Y2 Log", variable=widgets['y2_log'], command=lambda w=widgets, k=key: self.plot(w, k)).pack(side='left', padx=(0,10))
        ttk.Checkbutton(grid_frame, text="Grid:", variable=widgets['grid_on'], command=lambda w=widgets, k=key: self.on_grid_toggle(w, k)).pack(side='left', padx=(20, 2))
        widgets['grid_style_combo'] = ttk.Combobox(grid_frame, textvariable=widgets['grid_style'], values=['Light', 'Medium', 'Dark'], width=8, state='normal'); widgets['grid_style_combo'].pack(side='left'); widgets['grid_style_combo'].bind("<<ComboboxSelected>>", lambda event, w=widgets, k=key: self.plot(w, k))
        
        ttk.Separator(axis_frame).grid(row=4, column=0, columnspan=6, sticky='ew', pady=10)
        ttk.Label(axis_frame, text="X-Axis Range:").grid(row=5, column=0, sticky="w"); widgets['x_range_mode'] = tk.StringVar(value='auto'); ttk.Radiobutton(axis_frame, text="Auto", variable=widgets['x_range_mode'], value='auto', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=5, column=1, sticky="w"); ttk.Radiobutton(axis_frame, text="Manual:", variable=widgets['x_range_mode'], value='manual', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=5, column=2, sticky="w"); widgets['x_min'] = tk.StringVar(); widgets['x_max'] = tk.StringVar(); widgets['x_min_entry'] = ttk.Entry(axis_frame, textvariable=widgets['x_min'], width=8, state='disabled'); widgets['x_min_entry'].grid(row=5, column=3); widgets['x_min_entry'].bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(axis_frame, text="to").grid(row=5, column=4, padx=5); widgets['x_max_entry'] = ttk.Entry(axis_frame, textvariable=widgets['x_max'], width=8, state='disabled'); widgets['x_max_entry'].grid(row=5, column=5); widgets['x_max_entry'].bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k))
        ttk.Label(axis_frame, text="Y1-Axis Range:").grid(row=6, column=0, sticky="w"); widgets['y_range_mode'] = tk.StringVar(value='auto'); ttk.Radiobutton(axis_frame, text="Auto", variable=widgets['y_range_mode'], value='auto', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=6, column=1, sticky="w"); ttk.Radiobutton(axis_frame, text="Manual:", variable=widgets['y_range_mode'], value='manual', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=6, column=2, sticky="w"); widgets['y_min'] = tk.StringVar(); widgets['y_max'] = tk.StringVar(); widgets['y_min_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y_min'], width=8, state='disabled'); widgets['y_min_entry'].grid(row=6, column=3); widgets['y_min_entry'].bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(axis_frame, text="to").grid(row=6, column=4, padx=5); widgets['y_max_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y_max'], width=8, state='disabled'); widgets['y_max_entry'].grid(row=6, column=5); widgets['y_max_entry'].bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k))
        ttk.Label(axis_frame, text="Y2-Axis Range:").grid(row=7, column=0, sticky="w"); widgets['y2_range_mode'] = tk.StringVar(value='auto'); ttk.Radiobutton(axis_frame, text="Auto", variable=widgets['y2_range_mode'], value='auto', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=7, column=1, sticky="w"); ttk.Radiobutton(axis_frame, text="Manual:", variable=widgets['y2_range_mode'], value='manual', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=7, column=2, sticky="w"); widgets['y2_min'] = tk.StringVar(); widgets['y2_max'] = tk.StringVar(); widgets['y2_min_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y2_min'], width=8, state='disabled'); widgets['y2_min_entry'].grid(row=7, column=3); widgets['y2_min_entry'].bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); ttk.Label(axis_frame, text="to").grid(row=7, column=4, padx=5); widgets['y2_max_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y2_max'], width=8, state='disabled'); widgets['y2_max_entry'].grid(row=7, column=5); widgets['y2_max_entry'].bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k))

        layout_frame = ttk.LabelFrame(controls_frame, text="Plot Layout & Margins", padding=10); layout_frame.pack(fill='x', pady=5)
        widgets['use_custom_margins'] = tk.BooleanVar(value=False); ttk.Checkbutton(layout_frame, text="Set Custom Margins", variable=widgets['use_custom_margins'], command=lambda w=widgets: self.update_margin_entry_state(w)).grid(row=0, column=0, columnspan=4, sticky='w'); widgets['lmargin'] = tk.StringVar(); widgets['rmargin'] = tk.StringVar(); widgets['tmargin'] = tk.StringVar(); widgets['bmargin'] = tk.StringVar(); lmargin_spinbox = ttk.Spinbox(layout_frame, from_=-1000, to=1000, increment=10, textvariable=widgets['lmargin'], width=7, state='disabled'); lmargin_spinbox.grid(row=1, column=1); lmargin_spinbox.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); widgets['lmargin_entry'] = lmargin_spinbox; ttk.Label(layout_frame, text="Left (+):").grid(row=1, column=0, sticky='w'); rmargin_spinbox = ttk.Spinbox(layout_frame, from_=-1000, to=1000, increment=10, textvariable=widgets['rmargin'], width=7, state='disabled'); rmargin_spinbox.grid(row=1, column=3); rmargin_spinbox.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); widgets['rmargin_entry'] = rmargin_spinbox; ttk.Label(layout_frame, text="Right (-):").grid(row=1, column=2, sticky='w'); tmargin_spinbox = ttk.Spinbox(layout_frame, from_=-1000, to=1000, increment=10, textvariable=widgets['tmargin'], width=7, state='disabled'); tmargin_spinbox.grid(row=2, column=1); tmargin_spinbox.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); widgets['tmargin_entry'] = tmargin_spinbox; ttk.Label(layout_frame, text="Top (-):").grid(row=2, column=0, sticky='w'); bmargin_spinbox = ttk.Spinbox(layout_frame, from_=-1000, to=1000, increment=10, textvariable=widgets['bmargin'], width=7, state='disabled'); bmargin_spinbox.grid(row=2, column=3); bmargin_spinbox.bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k)); widgets['bmargin_entry'] = bmargin_spinbox; ttk.Label(layout_frame, text="Bottom (+):").grid(row=2, column=2, sticky='w'); ttk.Separator(layout_frame).grid(row=3, column=0, columnspan=4, sticky='ew', pady=10); widgets['lock_aspect_ratio'] = tk.BooleanVar(value=True); ttk.Checkbutton(layout_frame, text="Lock Aspect Ratio:", variable=widgets['lock_aspect_ratio'], command=lambda w=widgets: self.update_aspect_ratio_entry_state(w)).grid(row=4, column=0, columnspan=2, sticky='w'); widgets['aspect_ratio'] = tk.StringVar(value='0.75'); widgets['aspect_ratio_entry'] = ttk.Entry(layout_frame, textvariable=widgets['aspect_ratio'], width=8, state='normal'); widgets['aspect_ratio_entry'].grid(row=4, column=2); widgets['aspect_ratio_entry'].bind("<Return>", lambda event, w=widgets, k=key: self.plot(w, k))
        main_action_frame = ttk.Frame(controls_frame); main_action_frame.pack(fill='x', pady=10); ttk.Button(main_action_frame, text="Plot / Refresh", command=lambda w=widgets, k=key: self.plot(w, k)).pack(pady=5); replot_frame = ttk.Frame(controls_frame); replot_frame.pack(fill='x', pady=5); widgets['replot_interval'] = tk.StringVar(value='1000'); ttk.Label(replot_frame, text="Auto (ms):").pack(side='left'); ttk.Entry(replot_frame, textvariable=widgets['replot_interval'], width=8).pack(side='left', padx=5); widgets['start_button'] = ttk.Button(replot_frame, text="Start", command=lambda w=widgets, k=key: self.start_replot(w, k)); widgets['start_button'].pack(side='left'); widgets['stop_button'] = ttk.Button(replot_frame, text="Stop", state="disabled", command=lambda w=widgets: self.stop_replot(w)); widgets['stop_button'].pack(side='left', padx=5); ttk.Separator(controls_frame).pack(fill='x', pady=10); ttk.Button(controls_frame, text="Close Tab", command=lambda k=key: self.close_tab(k)).pack()
        
        export_frame = ttk.Frame(plot_frame); export_frame.pack(side='bottom', fill='x', pady=5); ttk.Button(export_frame, text="Save Plot...", command=lambda w=widgets, k=key: self.save_plot(w, k)).pack(side='left', padx=5); ttk.Button(export_frame, text="Copy to Clipboard", command=lambda w=widgets, k=key: self.copy_plot_to_clipboard(w, k)).pack(side='left', padx=5)
        widgets['plot_label'] = ttk.Label(plot_frame, text="Plot will appear here...", anchor='center'); widgets['plot_label'].pack(expand=True, fill='both')
        tab_data = {'widgets': widgets, 'plot_width': 600, 'plot_height': 400, 'resize_job': None, 'frame': tab_frame}
        plot_frame.bind("<Configure>", lambda event, k=key: self.on_plot_resize(event, k))
        self.tabs[key] = tab_data
        return tab_frame

    def _on_clean_data_toggle(self, widgets):
        if widgets['clean_data'].get():
            widgets['detect_headers'].set(False)
            widgets['detect_headers_cb'].config(state='disabled')
        else:
            widgets['detect_headers'].set(True)
            widgets['detect_headers_cb'].config(state='normal')

    def on_grid_toggle(self, widgets, key):
        state = 'normal' if widgets['grid_on'].get() else 'disabled'
        widgets['grid_style_combo'].config(state=state)
        self.plot(widgets, key)

    def toggle_checkbox(self, event, widgets, key):
        tree = widgets['tree']; region = tree.identify_region(event.x, event.y)
        if region != "tree": return
        item_id = tree.focus()
        if not item_id: return
        current_tags = list(tree.item(item_id, "tags"))
        if "checked" in current_tags: current_tags.remove("checked"); current_tags.append("unchecked"); tree.item(item_id, text="☐")
        else:
            if "unchecked" in current_tags: current_tags.remove("unchecked")
            current_tags.append("checked"); tree.item(item_id, text="☑")
        tree.item(item_id, tags=tuple(current_tags)); self.plot(widgets, key)

    def update_range_entry_state(self, widgets):
        widgets['x_min_entry'].config(state='normal' if widgets['x_range_mode'].get() == 'manual' else 'disabled'); widgets['x_max_entry'].config(state='normal' if widgets['x_range_mode'].get() == 'manual' else 'disabled')
        widgets['y_min_entry'].config(state='normal' if widgets['y_range_mode'].get() == 'manual' else 'disabled'); widgets['y_max_entry'].config(state='normal' if widgets['y_range_mode'].get() == 'manual' else 'disabled')
        widgets['y2_min_entry'].config(state='normal' if widgets['y2_range_mode'].get() == 'manual' else 'disabled'); widgets['y2_max_entry'].config(state='normal' if widgets['y2_range_mode'].get() == 'manual' else 'disabled')
        
    def update_margin_entry_state(self, widgets):
        state = 'normal' if widgets['use_custom_margins'].get() else 'disabled'
        widgets['lmargin_entry'].config(state=state); widgets['rmargin_entry'].config(state=state); widgets['tmargin_entry'].config(state=state); widgets['bmargin_entry'].config(state=state)
    def update_aspect_ratio_entry_state(self, widgets):
        state = 'normal' if widgets['lock_aspect_ratio'].get() else 'disabled'
        widgets['aspect_ratio_entry'].config(state=state)
    
    def _validate_numeric(self, value_str, field_name):
        if not value_str.strip(): return True
        try: float(value_str); return True
        except ValueError:
            messagebox.showwarning("Invalid Input", f"Please enter a valid number for '{field_name}'.\nYou entered: '{value_str}'")
            return False
        
    def generate_gnuplot_script(self, widgets, key, terminal_config):
        if widgets['x_range_mode'].get() == 'manual':
            if not self._validate_numeric(widgets['x_min'].get(), "X-Axis Min") or not self._validate_numeric(widgets['x_max'].get(), "X-Axis Max"): return None, None
        if widgets['y_range_mode'].get() == 'manual':
            if not self._validate_numeric(widgets['y_min'].get(), "Y1-Axis Min") or not self._validate_numeric(widgets['y_max'].get(), "Y1-Axis Max"): return None, None
        if widgets['y2_range_mode'].get() == 'manual':
            if not self._validate_numeric(widgets['y2_min'].get(), "Y2-Axis Min") or not self._validate_numeric(widgets['y2_max'].get(), "Y2-Axis Max"): return None, None
        if widgets['lock_aspect_ratio'].get():
            if not self._validate_numeric(widgets['aspect_ratio'].get(), "Aspect Ratio"): return None, None
        if widgets['use_custom_margins'].get():
            if not self._validate_numeric(widgets['lmargin'].get(), "Left Margin") or not self._validate_numeric(widgets['rmargin'].get(), "Right Margin") or not self._validate_numeric(widgets['tmargin'].get(), "Top Margin") or not self._validate_numeric(widgets['bmargin'].get(), "Bottom Margin"): return None, None

        y1_clauses, y2_clauses = [], []
        data_to_pipe = ""
        cleaned_data_cache = {}
        visible_datasets = []

        for item_id in widgets['tree'].get_children():
            if 'checked' in widgets['tree'].item(item_id, 'tags'):
                visible_datasets.append({
                    'values': widgets['tree'].item(item_id, 'values'),
                    'filepath': widgets['tree'].item(item_id, 'tags')[0]
                })

        for dataset in visible_datasets:
            if dataset['values'][6] == 'Yes' and dataset['filepath'] not in cleaned_data_cache:
                try:
                    with open(dataset['filepath'], 'r') as f:
                        content = f.read()
                    cleaned_content = content.replace('(', ' ').replace(')', ' ')
                    cleaned_data_cache[dataset['filepath']] = cleaned_content
                except Exception as e:
                    messagebox.showerror("File Error", f"Could not read or clean file:\n{dataset['filepath']}\n\nError: {e}")
                    return None, None

        for dataset in visible_datasets:
            values = dataset['values']
            filepath = dataset['filepath']
            
            if values[6] == 'Yes':
                plot_source = "'-'"
                if filepath in cleaned_data_cache:
                    data_to_pipe += cleaned_data_cache[filepath] + "\ne\n"
            else:
                plot_source = f"'{filepath}'"

            clause = f"{plot_source} using {values[1]}:{values[2]} with {values[4]} title '{values[5]}'"
            
            if values[3] == 'Y2': y2_clauses.append(clause + " axes x1y2")
            else: y1_clauses.append(clause + " axes x1y1")

        if not y1_clauses and not y2_clauses: return None, None
        full_plot_command = "plot " + ", ".join(y1_clauses + y2_clauses)
        y2_settings = ""
        if y2_clauses:
            y2_settings += "set ytics nomirror\nset y2tics\n"
            y2_settings += f'set y2label "{widgets["y2label"].get()}"\n'
            y2_settings += ("set logscale y2\n" if widgets['y2_log'].get() else "unset logscale y2\n")
            if widgets['y2_range_mode'].get() == 'manual' and widgets['y2_min'].get() and widgets['y2_max'].get(): y2_settings += f"set y2range [{widgets['y2_min'].get()}:{widgets['y2_max'].get()}]\n"
            else: y2_settings += "set autoscale y2\n"
        else: y2_settings = "unset y2tics\nunset y2label\n"
        
        if widgets['grid_on'].get():
            color_map = {'Light': 'gray40', 'Medium': 'gray20', 'Dark': 'gray0'}
            grid_color = color_map.get(widgets['grid_style'].get(), 'gray20')
            grid_settings = f'set grid back linetype 0 linecolor "{grid_color}"'
        else:
            grid_settings = 'unset grid'

        log_settings = ("set logscale x\n" if widgets['x_log'].get() else "unset logscale x\n") + ("set logscale y\n" if widgets['y_log'].get() else "unset logscale y\n")
        range_settings = ""
        if widgets['x_range_mode'].get() == 'manual' and widgets['x_min'].get() and widgets['x_max'].get(): range_settings += f"set xrange [{widgets['x_min'].get()}:{widgets['x_max'].get()}]\n"
        else: range_settings += "set autoscale x\n"
        if widgets['y_range_mode'].get() == 'manual' and widgets['y_min'].get() and widgets['y_max'].get(): range_settings += f"set yrange [{widgets['y_min'].get()}:{widgets['y_max'].get()}]\n"
        else: range_settings += "set autoscale y\n"
        margin_settings = ""
        if widgets['use_custom_margins'].get():
            if widgets['lmargin'].get() not in ('', '+', '-'): margin_settings += f"set lmargin {widgets['lmargin'].get()}\n"
            if widgets['rmargin'].get() not in ('', '+', '-'): margin_settings += f"set rmargin {widgets['rmargin'].get()}\n"
            if widgets['tmargin'].get() not in ('', '+', '-'): margin_settings += f"set tmargin {widgets['tmargin'].get()}\n"
            if widgets['bmargin'].get() not in ('', '+', '-'): margin_settings += f"set bmargin {widgets['bmargin'].get()}\n"
        else: margin_settings = "unset lmargin; unset rmargin; unset tmargin; unset bmargin\n"
        aspect_ratio_settings = f"set size ratio {widgets['aspect_ratio'].get()}" if widgets['lock_aspect_ratio'].get() and widgets['aspect_ratio'].get() else "set size noratio"

        script = f"""
            set terminal {terminal_config['term']} size {terminal_config['size']} enhanced font 'Verdana,10'
            set output '{terminal_config['output']}'
            {margin_settings}
            {aspect_ratio_settings}
            set xlabel "{widgets['xlabel'].get()}"
            set ylabel "{widgets['ylabel'].get()}"
            {log_settings}
            {grid_settings}
            {range_settings}
            {y2_settings}
            {full_plot_command}
            unset output
        """
        return script, data_to_pipe

    def plot(self, widgets, key):
        width, height = self.tabs[key]['plot_width'], self.tabs[key]['plot_height']
        image_filename = f"plot_{key}.png"
        terminal_config = {'term': 'pngcairo', 'size': f'{width},{height}', 'output': image_filename}
        
        gnuplot_script, data_to_pipe = self.generate_gnuplot_script(widgets, key, terminal_config)
        
        if not gnuplot_script: 
            return
        
        full_input = gnuplot_script
        if data_to_pipe:
            full_input += "\n" + data_to_pipe

        completed_process = subprocess.run(['gnuplot'], input=full_input, text=True, capture_output=True)

        if completed_process.returncode != 0: 
            messagebox.showerror("Gnuplot Error", completed_process.stderr)
            return
        try:
            img = Image.open(image_filename); photo = ImageTk.PhotoImage(img)
            plot_label = widgets['plot_label']; plot_label.config(text="", image=photo); plot_label.image = photo
        except Exception as e: messagebox.showerror("Image Error", f"An error occurred while loading the plot image:\n{e}")

    def save_plot(self, widgets, key):
        filepath = filedialog.asksaveasfilename(title="Save Plot As...", filetypes=(("PNG Image", "*.png"), ("SVG Vector Image", "*.svg"), ("PDF Document", "*.pdf"), ("Encapsulated PostScript", "*.eps")), defaultextension=".png")
        if not filepath: return
        _, extension = os.path.splitext(filepath)
        term_map = {'.png': 'pngcairo', '.svg': 'svg', '.pdf': 'pdfcairo', '.eps': 'postscript eps enhanced color'}
        if extension not in term_map: messagebox.showerror("Unsupported Format", f"File format '{extension}' is not supported."); return
        terminal_config = {'term': term_map[extension], 'size': '1024,768', 'output': filepath}

        gnuplot_script, data_to_pipe = self.generate_gnuplot_script(widgets, key, terminal_config)

        if not gnuplot_script: 
            messagebox.showwarning("Plotting Canceled", "Plotting was canceled due to no visible data or an invalid setting.")
            return
            
        full_input = gnuplot_script
        if data_to_pipe:
            full_input += "\n" + data_to_pipe

        completed_process = subprocess.run(['gnuplot'], input=full_input, text=True, capture_output=True)
        if completed_process.returncode != 0: messagebox.showerror("Gnuplot Error", completed_process.stderr)
        else: messagebox.showinfo("Success", f"Plot saved successfully to:\n{filepath}")
        
    def copy_plot_to_clipboard(self, widgets, key):
        image_filename = os.path.abspath(f"plot_{key}_cropped.png") 
        width, height = self.tabs[key]['plot_width'], self.tabs[key]['plot_height']
        terminal_config = {'term': 'pngcairo crop', 'size': f'{width},{height}', 'output': image_filename}
        gnuplot_script, data_to_pipe = self.generate_gnuplot_script(widgets, key, terminal_config)
        if not gnuplot_script: 
            messagebox.showwarning("Plotting Canceled", "Plotting was canceled due to no visible data or an invalid setting.")
            return
        
        full_input = gnuplot_script
        if data_to_pipe:
            full_input += "\n" + data_to_pipe

        completed_process = subprocess.run(['gnuplot'], input=full_input, text=True, capture_output=True)
        if completed_process.returncode != 0: messagebox.showerror("Gnuplot Error", completed_process.stderr); return
        if not os.path.exists(image_filename): messagebox.showerror("Error", "Cropped plot image not found."); return
        system = platform.system()
        try:
            if system == "Windows": command = f'powershell -command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile(\'{image_filename}\'))"'; subprocess.run(command, check=True, shell=True)
            elif system == "Darwin": command = f"""osascript -e 'set the clipboard to (read (POSIX file "{image_filename}") as TIFF picture)'"""; subprocess.run(command, check=True, shell=True)
            elif system == "Linux": subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'image/png', '-i', image_filename], check=True)
            else: messagebox.showwarning("Unsupported OS", f"Copy to clipboard is not supported on '{system}'."); return
            messagebox.showinfo("Success", "Cropped plot image copied to clipboard.")
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            error_msg = str(e)
            if "xclip" in error_msg: messagebox.showerror("Dependency Missing", "To copy on Linux, 'xclip' must be installed.\nPlease run: sudo apt-get install xclip")
            else: messagebox.showerror("Clipboard Error", f"Failed to copy image to clipboard.\n{error_msg}")
    
    def on_plot_resize(self, event, key):
        if key not in self.tabs: return
        tab_data = self.tabs[key]
        if tab_data['resize_job']: self.root.after_cancel(tab_data['resize_job'])
        tab_data['plot_width'] = max(event.width - 20, 100); tab_data['plot_height'] = max(event.height - 20, 100)
        tab_data['resize_job'] = self.root.after(250, lambda: self.plot(tab_data['widgets'], key))

    def browse_file(self, widgets):
        filename = filedialog.askopenfilename(title="Select a data file"); 
        if filename: widgets['filepath'].set(filename); widgets['plot_title'].set(os.path.basename(filename))
        
    def _get_column_header(self, filepath, y_col_index):
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line.startswith("# Time"):
                        headers = stripped_line.lstrip('#').split()
                        if 0 < y_col_index <= len(headers):
                            return headers[y_col_index - 1]
                        else:
                            return None
        except Exception:
            return None
        return None

    def add_dataset(self, widgets, key):
        filepath = widgets['filepath'].get()
        if not filepath: return
        
        plot_title_to_set = widgets['plot_title'].get()

        if widgets['detect_headers'].get():
            try:
                y_col = int(widgets['y_col'].get())
                header_title = self._get_column_header(filepath, y_col)
                if header_title:
                    plot_title_to_set = header_title
                    widgets['plot_title'].set(header_title)
            except (ValueError, FileNotFoundError):
                pass

        clean_state = 'Yes' if widgets['clean_data'].get() else 'No'
        values = (os.path.basename(filepath), widgets['x_col'].get(), widgets['y_col'].get(), widgets['y_axis_select'].get(), widgets['plot_style'].get(), plot_title_to_set, clean_state)
        widgets['tree'].insert('', 'end', values=values, tags=(filepath, 'checked'), text="☑")
        self.plot(widgets, key)

    def duplicate_dataset(self, widgets, key):
        selected_item = widgets['tree'].selection()
        if not selected_item: messagebox.showinfo("Info", "Please select a dataset to duplicate."); return
        
        values = list(widgets['tree'].item(selected_item[0], "values"))
        full_path = widgets['tree'].item(selected_item[0], "tags")[0]

        try:
            original_y_col = int(values[2])
            new_y_col = original_y_col + 1
            values[2] = str(new_y_col)

            plot_title_to_set = ""
            if widgets['detect_headers'].get():
                header_title = self._get_column_header(full_path, new_y_col)
                if header_title:
                    plot_title_to_set = header_title
            
            if not plot_title_to_set:
                original_title = values[5]
                base_title = original_title.split(' (col')[0]
                plot_title_to_set = f"{base_title} (col {new_y_col})"
            
            values[5] = plot_title_to_set

            widgets['tree'].insert('', 'end', values=tuple(values), tags=(full_path, 'checked'), text="☑")
            self.plot(widgets, key)

        except ValueError:
            messagebox.showerror("Error", f"Could not increment Y-column '{values[2]}'.")
        
    def update_dataset(self, widgets, key):
        selected_item = widgets['tree'].selection(); 
        if not selected_item: return
        filepath = widgets['filepath'].get()

        plot_title_to_set = widgets['plot_title'].get()

        if widgets['detect_headers'].get():
            try:
                y_col = int(widgets['y_col'].get())
                header_title = self._get_column_header(filepath, y_col)
                if header_title:
                    plot_title_to_set = header_title
                    widgets['plot_title'].set(header_title)
            except (ValueError, FileNotFoundError):
                pass

        clean_state = 'Yes' if widgets['clean_data'].get() else 'No'
        values = (os.path.basename(filepath), widgets['x_col'].get(), widgets['y_col'].get(), widgets['y_axis_select'].get(), widgets['plot_style'].get(), plot_title_to_set, clean_state)
        current_tags = widgets['tree'].item(selected_item, 'tags'); visibility_tag = 'checked' if 'checked' in current_tags else 'unchecked'
        widgets['tree'].item(selected_item, values=values, tags=(filepath, visibility_tag))
        widgets['update_button'].config(state='disabled'); widgets['duplicate_button'].config(state='disabled'); widgets['remove_button'].config(state='disabled')
        self.plot(widgets, key)

    def remove_dataset(self, widgets, key):
        selected_item = widgets['tree'].selection()
        if selected_item: 
            widgets['tree'].delete(selected_item)
            widgets['update_button'].config(state='disabled'); widgets['duplicate_button'].config(state='disabled'); widgets['remove_button'].config(state='disabled')
            self.plot(widgets, key)
        
    def on_tree_select(self, event, widgets):
        selected_item = widgets['tree'].selection(); 
        if not selected_item: 
            widgets['update_button'].config(state='disabled'); widgets['duplicate_button'].config(state='disabled'); widgets['remove_button'].config(state='disabled')
            return
        widgets['update_button'].config(state='normal'); widgets['duplicate_button'].config(state='normal'); widgets['remove_button'].config(state='normal')
        values = widgets['tree'].item(selected_item, "values"); full_path = widgets['tree'].item(selected_item, "tags")[0]
        widgets['filepath'].set(full_path); widgets['x_col'].set(values[1]); widgets['y_col'].set(values[2]); widgets['y_axis_select'].set(values[3]); widgets['plot_style'].set(values[4]); widgets['plot_title'].set(values[5])

        # Set the 'Clean Data' checkbox based on the selected dataset
        widgets['clean_data'].set(True if values[6] == 'Yes' else False)

        # Now, ONLY update the enabled/disabled state of the 'Detect Headers' checkbox,
        # without changing its checked/unchecked value. Its value will now persist.
        if widgets['clean_data'].get():
            widgets['detect_headers_cb'].config(state='disabled')
        else:
            widgets['detect_headers_cb'].config(state='normal')

    def start_replot(self, widgets, key):
        self.stop_replot(widgets); self.auto_replotting = True; widgets['start_button'].config(state="disabled"); widgets['stop_button'].config(state="normal"); self.auto_replot_loop(widgets, key)
    def stop_replot(self, widgets):
        self.auto_replotting = False
        if self.active_auto_replot_job: self.root.after_cancel(self.active_auto_replot_job); self.active_auto_replot_job = None
        widgets['start_button'].config(state="normal"); widgets['stop_button'].config(state="disabled")

    def auto_replot_loop(self, widgets, key):
        if self.auto_replotting:
            self.plot(widgets, key)
            try: 
                interval = int(widgets['replot_interval'].get())
                if interval <= 0:
                    messagebox.showwarning("Invalid Interval", "Auto-replot interval must be a positive number.")
                    self.stop_replot(widgets)
                    return
                self.active_auto_replot_job = self.root.after(interval, lambda: self.auto_replot_loop(widgets, key))
            except ValueError: 
                messagebox.showwarning("Invalid Interval", "Please enter a valid whole number for the auto-replot interval (in ms).")
                self.stop_replot(widgets)

if __name__ == "__main__":
    root = tk.Tk()
    app = GnuplotApp(root)
    root.mainloop()


