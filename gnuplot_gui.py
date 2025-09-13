#!/usr/bin/env python3
"""
Filename: gnuplot_gui.py
Author: G. Vijaya Kumar
Date: Sep 7, 2025
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
import json
import re
import pandas as pd
from tempfile import NamedTemporaryFile
import threading
import queue

# --- Column Selector Dialog ---
class ColumnSelectorDialog(tk.Toplevel):
    def __init__(self, parent, all_columns):
        super().__init__(parent)
        self.title("Select Columns to Monitor")
        self.result = None
        self.all_columns = sorted(all_columns)

        self.transient(parent)
        self.grab_set()
        
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill='both', expand=True)

        ttk.Label(main_frame, text="Select the columns you want to monitor:", wraplength=380).pack(pady=(0, 10))

        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill='both', expand=True)
        
        self.listbox = tk.Listbox(list_frame, selectmode='multiple', height=15, exportselection=False)
        for col in self.all_columns:
            self.listbox.insert('end', col)
        
        list_scroll_y = ttk.Scrollbar(list_frame, orient='vertical', command=self.listbox.yview)
        list_scroll_x = ttk.Scrollbar(list_frame, orient='horizontal', command=self.listbox.xview)
        self.listbox.config(yscrollcommand=list_scroll_y.set, xscrollcommand=list_scroll_x.set)

        list_scroll_y.pack(side='right', fill='y')
        list_scroll_x.pack(side='bottom', fill='x')
        self.listbox.pack(side='left', fill='both', expand=True)

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=10)

        ttk.Button(button_frame, text="Select All", command=self.select_all).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Deselect All", command=self.deselect_all).pack(side='left', padx=5)
        ttk.Button(button_frame, text="OK", command=self.on_ok).pack(side='right', padx=5)

        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.wait_window(self)

    def on_ok(self):
        self.result = [self.listbox.get(i) for i in self.listbox.curselection()]
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()

    def select_all(self):
        self.listbox.selection_set(0, 'end')

    def deselect_all(self):
        self.listbox.selection_clear(0, 'end')

# --- Logfile Parser Class ---
class LogfileParser:
    def __init__(self, filepath=None):
        self.filepath = filepath
        # Regex to find the main time steps
        self.time_re = re.compile(r"^\s*Time = (\S+)\s*$")
        # Regex for solver residuals
        self.solver_re = re.compile(
            r"Solving for (\S+), Initial residual = (\S+), Final residual = (\S+), No Iterations\s+(\d+)"
        )
        # Regex for functionObjects with a vector value. Captures name and value string.
        self.fo_vector_re = re.compile(r"^\s*(.+?)\s*=\s*\((.+)\)\s*$")
        # Regex for functionObjects with a single scalar value. Captures name and value string.
        self.fo_scalar_re = re.compile(r"^\s*(.+?)\s*=\s*(\S+)\s*$")

    def _clean_column_name(self, raw_name):
        """Creates a clean, valid column name from a raw string found in the log file."""
        # Replace common separators and brackets with underscores
        name = re.sub(r'[\s\(\),]', '_', raw_name)
        # Remove 'of' if it's a whole word, surrounded by underscores
        name = re.sub(r'_of_', '_', name)
        # Collapse multiple underscores into one
        name = re.sub(r'__+', '_', name)
        # Remove leading/trailing underscores
        name = name.strip('_')
        return name

    def parse_lines(self, lines, monitored_columns=None):
        """Parses a list of string lines and returns a list of record dictionaries."""
        records = []
        current_record = {}
        
        # Using a set for faster lookups if a filter is provided
        monitored_set = set(monitored_columns) if monitored_columns else None
        
        for line in lines:
            # Check for a new time step
            time_match = self.time_re.match(line)
            if time_match:
                if current_record: # Save the previous record if it exists
                    records.append(current_record)
                current_record = {'Time': float(time_match.group(1))}
                continue

            if not current_record: # Skip lines before the first "Time ="
                continue

            # Skip continuity error lines to avoid creating unnecessary columns
            if "time step continuity errors" in line:
                continue
                
            # Check for solver lines
            solver_match = self.solver_re.search(line)
            if solver_match:
                var, i_res, f_res, iters = solver_match.groups()
                col_name = f'{var}_initial_residual'
                if monitored_set is None or col_name in monitored_set:
                    current_record[col_name] = float(i_res)
                continue # Move to next line after match
            
            # Check for function object lines (vector or scalar)
            line_stripped = line.strip()

            # Try vector match first, as its pattern is more specific
            vector_match = self.fo_vector_re.match(line_stripped)
            if vector_match:
                name_raw, values_str = vector_match.groups()
                # Exclude solver lines which can sometimes match this regex
                if "Solving for" not in name_raw:
                    name = self._clean_column_name(name_raw)
                    try:
                        values = [float(v) for v in values_str.split()]
                        if monitored_set is None or f'{name}_x' in monitored_set:
                            current_record[f'{name}_x'] = values[0]
                        if len(values) > 1 and (monitored_set is None or f'{name}_y' in monitored_set):
                            current_record[f'{name}_y'] = values[1]
                        if len(values) > 2 and (monitored_set is None or f'{name}_z' in monitored_set):
                            current_record[f'{name}_z'] = values[2]
                    except (ValueError, IndexError):
                        pass # Ignore if values are not numbers
                continue
            
            # Then try scalar match
            scalar_match = self.fo_scalar_re.match(line_stripped)
            if scalar_match:
                name_raw, val_str = scalar_match.groups()
                # Exclude solver lines
                if "Solving for" not in name_raw:
                    name = self._clean_column_name(name_raw)
                    if monitored_set is None or name in monitored_set:
                        try:
                            val = float(val_str)
                            current_record[name] = val
                        except (ValueError, TypeError):
                            pass
                continue

        if current_record: # Append the last record
            records.append(current_record)
            
        return records

    def parse(self):
        try:
            with open(self.filepath, 'r') as f:
                lines = f.readlines()
                byte_offset = f.tell()
            
            records = self.parse_lines(lines)

            if not records:
                return None, "No data could be parsed. Check the logfile format.", 0

            df = pd.DataFrame.from_records(records)
            df = df.ffill()
            df = df.sort_values(by='Time').drop_duplicates(subset='Time', keep='last')
            
            if 'Time' not in df.columns or df.empty:
                 return None, "Parsing resulted in an empty dataset or 'Time' column is missing.", 0

            return df, None, byte_offset

        except Exception as e:
            return None, f"An error occurred during parsing: {e}", 0

# --- Main Application Class ---
class GnuplotApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Embedded Gnuplot GUI V28.0") # Version bump!
        self.root.geometry("1200x800")
        
        self.auto_replotting = False
        self.active_auto_replot_job = None
        
        self.tabs = {}
        self.tab_counter = 0
        self.right_clicked_tab_id = None
        
        self.log_queue = queue.Queue()

        self.menu_bar = tk.Menu(self.root)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.file_menu.add_command(label="Load Session...", command=self.load_session)
        self.file_menu.add_command(label="Save Session As...", command=self.save_session)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self._on_closing)
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)
        self.root.config(menu=self.menu_bar)

        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        self.tab_menu = tk.Menu(self.root, tearoff=0)
        self.tab_menu.add_command(label="Rename Tab", command=self.rename_tab_popup)
        self.notebook.bind("<Button-3>", self.show_tab_menu)

        # --- Corrected Initialization ---
        self.tab_counter += 1
        first_title = f"Plot {self.tab_counter}"
        first_key = f"tab{self.tab_counter}"
        first_tab_frame = self.create_plot_tab(first_title, first_key)
        self.notebook.add(first_tab_frame, text=first_title)
        
        self.plus_tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.plus_tab_frame, text='+')
        
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self._process_log_queue()

    def _on_closing(self):
        response = messagebox.askyesnocancel("Quit", "Do you want to save your session before quitting?")
        
        if response is True:
            if not self.save_session():
                return
        elif response is None:
            return

        for key in list(self.tabs.keys()):
            self._stop_log_tail(key)
        
        # Clean up temporary files
        for tab_data in self.tabs.values():
            if tab_data.get('temp_file_path') and os.path.exists(tab_data['temp_file_path']):
                try:
                    os.remove(tab_data['temp_file_path'])
                except OSError as e:
                    print(f"Error removing temporary file: {e}")
        self.root.destroy()

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
        entry.bind("<Escape>", lambda e: popup.destroy())
        popup.bind("<Escape>", lambda e: popup.destroy())

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
        return key

    def close_tab(self, key):
        if len(self.notebook.tabs()) <= 2:
            messagebox.showwarning("Action Blocked", "Cannot close the last plot tab.")
            return
        
        self._stop_log_tail(key)

        tab_data = self.tabs.get(key)
        if tab_data and tab_data.get('temp_file_path') and os.path.exists(tab_data['temp_file_path']):
             try:
                os.remove(tab_data['temp_file_path'])
             except OSError as e:
                print(f"Error removing temp file on tab close: {e}")

        frame_to_close = self.tabs[key]['frame']
        self.notebook.forget(frame_to_close)
        del self.tabs[key]

    def on_tab_changed(self, event):
        try:
            selected_tab_text = event.widget.tab(event.widget.select(), "text")
            if selected_tab_text == '+': self.add_new_tab()
        except tk.TclError: pass
            
    def _switch_mode(self, widgets, key):
        mode = widgets['mode'].get()
        tab_data = self.tabs[key]
        plot_display_panedwindow = tab_data['plot_display_panedwindow']
        log_viewer_id = str(tab_data['log_viewer_frame'])

        if mode == "Normal":
            if log_viewer_id in plot_display_panedwindow.panes():
                plot_display_panedwindow.forget(log_viewer_id)
            self._stop_log_tail(key)
            widgets['logfile_mode_frame'].pack_forget()
            widgets['normal_mode_frame'].pack(fill='x', expand=True)
        else: # Plot Logfile
            if log_viewer_id not in plot_display_panedwindow.panes():
                 plot_display_panedwindow.add(tab_data['log_viewer_frame'], weight=1)
            
            self.root.update_idletasks()
            sash_pos = int(plot_display_panedwindow.winfo_height() * 0.8) if plot_display_panedwindow.winfo_height() > 1 else 300
            plot_display_panedwindow.sashpos(0, sash_pos)

            widgets['normal_mode_frame'].pack_forget()
            widgets['logfile_mode_frame'].pack(fill='x', expand=True)

    def create_plot_tab(self, title, key):
        tab_frame = ttk.Frame(self.notebook)
        paned_window = ttk.PanedWindow(tab_frame, orient='horizontal')
        paned_window.pack(expand=True, fill='both')

        scroll_container = ttk.Frame(paned_window)
        paned_window.add(scroll_container, weight=1)

        canvas = tk.Canvas(scroll_container)
        scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        controls_frame = ttk.Frame(canvas, padding="10")
        canvas_frame_id = canvas.create_window((0, 0), window=controls_frame, anchor="nw")
        
        def on_frame_configure(event): canvas.configure(scrollregion=canvas.bbox("all"))
        def on_canvas_configure(event): canvas.itemconfig(canvas_frame_id, width=event.width)
        controls_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_canvas_configure)
        
        def _on_mousewheel(event):
            if platform.system() == 'Windows': canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            elif platform.system() == 'Darwin': canvas.yview_scroll(int(-1*event.delta), "units")
            else:
                if event.num == 4: canvas.yview_scroll(-1, "units")
                elif event.num == 5: canvas.yview_scroll(1, "units")

        def _bind_mousewheel(event): canvas.bind_all("<MouseWheel>", _on_mousewheel)
        def _unbind_mousewheel(event): canvas.unbind_all("<MouseWheel>")
        canvas.bind('<Enter>', _bind_mousewheel)
        canvas.bind('<Leave>', _unbind_mousewheel)
        
        # --- NEW: Vertical PanedWindow for Plot and Log ---
        plot_display_panedwindow = ttk.PanedWindow(paned_window, orient='vertical')
        paned_window.add(plot_display_panedwindow, weight=2)
        
        plot_image_frame = ttk.Frame(plot_display_panedwindow, padding="10")
        plot_display_panedwindow.add(plot_image_frame, weight=4) # 80% weight

        log_viewer_frame = ttk.LabelFrame(plot_display_panedwindow, text="Log File Output (tail -f)", padding=10)
        # Initially add it, but it will be hidden/shown by _switch_mode
        plot_display_panedwindow.add(log_viewer_frame, weight=1) # 20% weight

        widgets = {}
        
        # Define plot_callback early
        plot_callback = lambda event=None, w=widgets, k=key: self.plot(w, k)
        
        # --- Mode Selection ---
        mode_frame = ttk.LabelFrame(controls_frame, text="Mode", padding=10)
        mode_frame.pack(fill='x', pady=5)
        widgets['mode'] = tk.StringVar(value="Normal")
        ttk.Radiobutton(mode_frame, text="Normal", variable=widgets['mode'], value="Normal", command=lambda w=widgets, k=key: self._switch_mode(w, k)).pack(side='left', padx=5)
        ttk.Radiobutton(mode_frame, text="Plot Logfile", variable=widgets['mode'], value="Plot Logfile", command=lambda w=widgets, k=key: self._switch_mode(w, k)).pack(side='left', padx=5)

        # --- Frame Containers for Modes ---
        widgets['normal_mode_frame'] = ttk.Frame(controls_frame)
        widgets['normal_mode_frame'].pack(fill='x', expand=True)
        
        widgets['logfile_mode_frame'] = ttk.Frame(controls_frame)
        # Initially hidden
        
        # --- NORMAL MODE WIDGETS ---
        normal_frame = widgets['normal_mode_frame']

        # Define a callback for auto-updating datasets
        update_callback = lambda event=None, w=widgets, k=key: self.update_dataset(w, k)
        
        global_settings_frame = ttk.LabelFrame(normal_frame, text="Global Plot & Data Settings", padding=10)
        global_settings_frame.pack(fill='x', pady=5)
        
        ttk.Label(global_settings_frame, text="Separator:").grid(row=0, column=0, sticky='w', pady=(0, 2))
        widgets['separator'] = tk.StringVar(value='whitespace')
        separator_combo = ttk.Combobox(global_settings_frame, textvariable=widgets['separator'], values=['whitespace', ','], width=12)
        separator_combo.grid(row=0, column=1, sticky='w', pady=(0, 2))
        separator_combo.bind("<<ComboboxSelected>>", lambda event, w=widgets: self._on_separator_change(w))

        ttk.Label(global_settings_frame, text="Plot Title:").grid(row=1, column=0, sticky='w')
        widgets['plot_global_title'] = tk.StringVar()
        plot_global_title_entry = ttk.Entry(global_settings_frame, textvariable=widgets['plot_global_title'])
        plot_global_title_entry.grid(row=1, column=1, sticky='ew')
        global_settings_frame.columnconfigure(1, weight=1)
        plot_global_title_entry.bind("<Return>", plot_callback)
        
        font_frame = ttk.LabelFrame(normal_frame, text="Font Sizes", padding=10)
        font_frame.pack(fill='x', pady=5)
        
        ttk.Label(font_frame, text="Title:").grid(row=0, column=0, sticky='w')
        widgets['title_font_size'] = tk.StringVar(value='14')
        ttk.Spinbox(font_frame, from_=8, to=36, increment=1, textvariable=widgets['title_font_size'], width=5, command=plot_callback).grid(row=0, column=1, sticky='w')
        
        ttk.Label(font_frame, text="Axes:").grid(row=0, column=2, sticky='w', padx=(10, 0))
        widgets['axes_font_size'] = tk.StringVar(value='12')
        ttk.Spinbox(font_frame, from_=8, to=24, increment=1, textvariable=widgets['axes_font_size'], width=5, command=plot_callback).grid(row=0, column=3, sticky='w')

        ttk.Label(font_frame, text="Legend:").grid(row=0, column=4, sticky='w', padx=(10, 0))
        widgets['legend_font_size'] = tk.StringVar(value='12')
        ttk.Spinbox(font_frame, from_=8, to=24, increment=1, textvariable=widgets['legend_font_size'], width=5, command=plot_callback).grid(row=0, column=5, sticky='w')

        dataset_frame = ttk.LabelFrame(normal_frame, text="Datasets", padding=10); dataset_frame.pack(fill='x', pady=5)
        columns = ("file", "x_col", "y_col", "axis", "style", "title", "clean")
        widgets['tree'] = ttk.Treeview(dataset_frame, columns=columns, show="tree headings", height=4)
        widgets['tree'].heading("#0", text="Show"); widgets['tree'].column("#0", width=40, anchor='center', stretch=False)
        widgets['tree'].heading("file", text="File"); widgets['tree'].heading("x_col", text="X"); widgets['tree'].heading("y_col", text="Y"); widgets['tree'].heading("axis", text="Axis"); widgets['tree'].heading("style", text="Style"); widgets['tree'].heading("title", text="Title"); widgets['tree'].heading("clean", text="Clean")
        widgets['tree'].column("file", width=100); widgets['tree'].column("x_col", width=30, anchor='center'); widgets['tree'].column("y_col", width=30, anchor='center'); widgets['tree'].column("axis", width=40, anchor='center'); widgets['tree'].column("style", width=60); widgets['tree'].column("clean", width=40, anchor='center'); widgets['tree'].pack(fill='x')
        widgets['tree'].bind("<<TreeviewSelect>>", lambda event, w=widgets: self.on_tree_select(event, w))
        widgets['tree'].bind("<Button-1>", lambda event, w=widgets, k=key: self.toggle_checkbox(event, w, k))

        editor_frame = ttk.LabelFrame(normal_frame, text="Dataset Editor", padding=10); editor_frame.pack(fill='x', pady=5)
        widgets['filepath'] = tk.StringVar(); filepath_entry = ttk.Entry(editor_frame, textvariable=widgets['filepath'], width=25); filepath_entry.grid(row=0, column=1, sticky="ew", columnspan=3); filepath_entry.bind("<Return>", plot_callback); ttk.Label(editor_frame, text="Data File:").grid(row=0, column=0, sticky="w", pady=2); ttk.Button(editor_frame, text="Browse...", command=lambda w=widgets: self.browse_file(w)).grid(row=0, column=4, padx=5)
        
        widgets['x_col'] = tk.StringVar(value='1'); x_col_entry = ttk.Entry(editor_frame, textvariable=widgets['x_col'], width=5); x_col_entry.grid(row=1, column=1, sticky="w"); x_col_entry.bind("<Return>", update_callback); ttk.Label(editor_frame, text="X Col:").grid(row=1, column=0, sticky="w", pady=2)
        
        widgets['y_col'] = tk.StringVar(value='2'); y_col_entry = ttk.Entry(editor_frame, textvariable=widgets['y_col'], width=5); y_col_entry.grid(row=1, column=3, sticky="w"); y_col_entry.bind("<Return>", update_callback); ttk.Label(editor_frame, text="Y Col:").grid(row=1, column=2, sticky="e", pady=2, padx=5)
        
        widgets['y_axis_select'] = tk.StringVar(value='Y1'); ttk.Label(editor_frame, text="Axis:").grid(row=1, column=4, sticky="e", padx=(10,2)); 
        axis_combo = ttk.Combobox(editor_frame, textvariable=widgets['y_axis_select'], values=['Y1', 'Y2'], width=4)
        axis_combo.grid(row=1, column=5, sticky="w")
        axis_combo.bind("<<ComboboxSelected>>", update_callback)

        widgets['plot_style'] = tk.StringVar(value='lines'); ttk.Label(editor_frame, text="Plot Style:").grid(row=2, column=0, sticky="w", pady=2); 
        style_combo = ttk.Combobox(editor_frame, textvariable=widgets['plot_style'], values=['lines', 'points', 'linespoints', 'dots', 'impulses'], width=15)
        style_combo.grid(row=2, column=1, sticky="ew", columnspan=2)
        style_combo.bind("<<ComboboxSelected>>", update_callback)

        widgets['plot_title'] = tk.StringVar(); plot_title_entry = ttk.Entry(editor_frame, textvariable=widgets['plot_title'], width=20); plot_title_entry.grid(row=3, column=1, sticky="ew", columnspan=3); plot_title_entry.bind("<Return>", update_callback); ttk.Label(editor_frame, text="Title:").grid(row=3, column=0, sticky="w", pady=2)
        
        options_frame = ttk.Frame(editor_frame); options_frame.grid(row=4, column=0, columnspan=6, sticky='w', pady=5)
        widgets['clean_data'] = tk.BooleanVar(value=False)
        widgets['detect_headers'] = tk.BooleanVar(value=True)
        
        widgets['clean_cb'] = ttk.Checkbutton(options_frame, text="Clean Vector Data ( )", variable=widgets['clean_data'], command=lambda w=widgets: self._on_clean_data_toggle(w))
        widgets['clean_cb'].pack(side='left')
        
        widgets['detect_headers_cb'] = ttk.Checkbutton(options_frame, text="Detect Column Headers", variable=widgets['detect_headers'])
        widgets['detect_headers_cb'].pack(side='left', padx=10)

        dataset_actions_frame = ttk.Frame(normal_frame); dataset_actions_frame.pack(fill='x', pady=5)
        ttk.Button(dataset_actions_frame, text="Add Dataset", command=lambda w=widgets, k=key: self.add_dataset(w, k)).pack(side='left', padx=5)
        widgets['update_button'] = ttk.Button(dataset_actions_frame, text="Update Selected", state="disabled", command=lambda w=widgets, k=key: self.update_dataset(w, k)); widgets['update_button'].pack(side='left', padx=5)
        widgets['duplicate_button'] = ttk.Button(dataset_actions_frame, text="Duplicate Selected", state="disabled", command=lambda w=widgets, k=key: self.duplicate_dataset(w, k)); widgets['duplicate_button'].pack(side='left', padx=5)
        widgets['load_all_button'] = ttk.Button(dataset_actions_frame, text="Load All Columns", state="disabled", command=lambda w=widgets, k=key: self.load_all_columns(w, k)); widgets['load_all_button'].pack(side='left', padx=5)
        widgets['remove_button'] = ttk.Button(dataset_actions_frame, text="Remove Selected", state="disabled", command=lambda w=widgets, k=key: self.remove_dataset(w, k)); widgets['remove_button'].pack(side='right')

        axis_frame = ttk.LabelFrame(normal_frame, text="Axes Settings", padding=10); axis_frame.pack(fill='x', pady=5)
        widgets['xlabel'] = tk.StringVar(); xlabel_entry = ttk.Entry(axis_frame, textvariable=widgets['xlabel'], width=30); xlabel_entry.grid(row=0, column=1, columnspan=5, sticky="ew"); xlabel_entry.bind("<Return>", plot_callback); ttk.Label(axis_frame, text="X-Axis Title:").grid(row=0, column=0, sticky="w", pady=2)
        widgets['ylabel'] = tk.StringVar(); ylabel_entry = ttk.Entry(axis_frame, textvariable=widgets['ylabel'], width=30); ylabel_entry.grid(row=1, column=1, columnspan=5, sticky="ew"); ylabel_entry.bind("<Return>", plot_callback); ttk.Label(axis_frame, text="Y1-Axis Title:").grid(row=1, column=0, sticky="w", pady=2)
        widgets['y2label'] = tk.StringVar(); y2label_entry = ttk.Entry(axis_frame, textvariable=widgets['y2label'], width=30); y2label_entry.grid(row=2, column=1, columnspan=5, sticky="ew"); y2label_entry.bind("<Return>", plot_callback); ttk.Label(axis_frame, text="Y2-Axis Title:").grid(row=2, column=0, sticky="w", pady=2)
        widgets['x_log'] = tk.BooleanVar(); widgets['y_log'] = tk.BooleanVar(); widgets['y2_log'] = tk.BooleanVar(); widgets['grid_on'] = tk.BooleanVar(value=True); widgets['grid_style'] = tk.StringVar(value='Medium')
        
        grid_frame = ttk.Frame(axis_frame); grid_frame.grid(row=3, column=0, columnspan=6, sticky='w', pady=5)
        ttk.Checkbutton(grid_frame, text="X Log", variable=widgets['x_log'], command=plot_callback).pack(side='left', padx=(0,10))
        ttk.Checkbutton(grid_frame, text="Y1 Log", variable=widgets['y_log'], command=plot_callback).pack(side='left', padx=(0,10))
        ttk.Checkbutton(grid_frame, text="Y2 Log", variable=widgets['y2_log'], command=plot_callback).pack(side='left', padx=(0,10))
        ttk.Checkbutton(grid_frame, text="Grid:", variable=widgets['grid_on'], command=lambda w=widgets, k=key: self.on_grid_toggle(w, k)).pack(side='left', padx=(20, 2))
        widgets['grid_style_combo'] = ttk.Combobox(grid_frame, textvariable=widgets['grid_style'], values=['Light', 'Medium', 'Dark'], width=8, state='normal'); widgets['grid_style_combo'].pack(side='left'); widgets['grid_style_combo'].bind("<<ComboboxSelected>>", plot_callback)
        
        ttk.Separator(axis_frame).grid(row=4, column=0, columnspan=6, sticky='ew', pady=10)
        ttk.Label(axis_frame, text="X-Axis Range:").grid(row=5, column=0, sticky="w"); widgets['x_range_mode'] = tk.StringVar(value='auto'); ttk.Radiobutton(axis_frame, text="Auto", variable=widgets['x_range_mode'], value='auto', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=5, column=1, sticky="w"); ttk.Radiobutton(axis_frame, text="Manual:", variable=widgets['x_range_mode'], value='manual', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=5, column=2, sticky="w"); widgets['x_min'] = tk.StringVar(); widgets['x_max'] = tk.StringVar(); widgets['x_min_entry'] = ttk.Entry(axis_frame, textvariable=widgets['x_min'], width=8, state='disabled'); widgets['x_min_entry'].grid(row=5, column=3); widgets['x_min_entry'].bind("<Return>", plot_callback); ttk.Label(axis_frame, text="to").grid(row=5, column=4, padx=5); widgets['x_max_entry'] = ttk.Entry(axis_frame, textvariable=widgets['x_max'], width=8, state='disabled'); widgets['x_max_entry'].grid(row=5, column=5); widgets['x_max_entry'].bind("<Return>", plot_callback)
        ttk.Label(axis_frame, text="Y1-Axis Range:").grid(row=6, column=0, sticky="w"); widgets['y_range_mode'] = tk.StringVar(value='auto'); ttk.Radiobutton(axis_frame, text="Auto", variable=widgets['y_range_mode'], value='auto', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=6, column=1, sticky="w"); ttk.Radiobutton(axis_frame, text="Manual:", variable=widgets['y_range_mode'], value='manual', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=6, column=2, sticky="w"); widgets['y_min'] = tk.StringVar(); widgets['y_max'] = tk.StringVar(); widgets['y_min_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y_min'], width=8, state='disabled'); widgets['y_min_entry'].grid(row=6, column=3); widgets['y_min_entry'].bind("<Return>", plot_callback); ttk.Label(axis_frame, text="to").grid(row=6, column=4, padx=5); widgets['y_max_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y_max'], width=8, state='disabled'); widgets['y_max_entry'].grid(row=6, column=5); widgets['y_max_entry'].bind("<Return>", plot_callback)
        ttk.Label(axis_frame, text="Y2-Axis Range:").grid(row=7, column=0, sticky="w"); widgets['y2_range_mode'] = tk.StringVar(value='auto'); ttk.Radiobutton(axis_frame, text="Auto", variable=widgets['y2_range_mode'], value='auto', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=7, column=1, sticky="w"); ttk.Radiobutton(axis_frame, text="Manual:", variable=widgets['y2_range_mode'], value='manual', command=lambda w=widgets: self.update_range_entry_state(w)).grid(row=7, column=2, sticky="w"); widgets['y2_min'] = tk.StringVar(); widgets['y2_max'] = tk.StringVar(); widgets['y2_min_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y2_min'], width=8, state='disabled'); widgets['y2_min_entry'].grid(row=7, column=3); widgets['y2_min_entry'].bind("<Return>", plot_callback); ttk.Label(axis_frame, text="to").grid(row=7, column=4, padx=5); widgets['y2_max_entry'] = ttk.Entry(axis_frame, textvariable=widgets['y2_max'], width=8, state='disabled'); widgets['y2_max_entry'].grid(row=7, column=5); widgets['y2_max_entry'].bind("<Return>", plot_callback)

        layout_frame = ttk.LabelFrame(normal_frame, text="Plot Layout & Margins", padding=10)
        layout_frame.pack(fill='x', pady=5)
        widgets['use_custom_margins'] = tk.BooleanVar(value=False)
        ttk.Checkbutton(layout_frame, text="Set Custom Margins", variable=widgets['use_custom_margins'], command=lambda w=widgets: self.update_margin_entry_state(w)).grid(row=0, column=0, columnspan=4, sticky='w')
        
        widgets['lmargin'] = tk.StringVar(value='10')
        widgets['rmargin'] = tk.StringVar(value='2')
        widgets['tmargin'] = tk.StringVar(value='2')
        widgets['bmargin'] = tk.StringVar(value='5')
        
        lmargin_spinbox = ttk.Spinbox(layout_frame, from_=0, to=1000, increment=1, textvariable=widgets['lmargin'], width=7, state='disabled', command=plot_callback)
        lmargin_spinbox.grid(row=1, column=1); lmargin_spinbox.bind("<Return>", plot_callback); widgets['lmargin_entry'] = lmargin_spinbox
        ttk.Label(layout_frame, text="Left (+):").grid(row=1, column=0, sticky='w')

        rmargin_spinbox = ttk.Spinbox(layout_frame, from_=0, to=1000, increment=1, textvariable=widgets['rmargin'], width=7, state='disabled', command=plot_callback)
        rmargin_spinbox.grid(row=1, column=3); rmargin_spinbox.bind("<Return>", plot_callback); widgets['rmargin_entry'] = rmargin_spinbox
        ttk.Label(layout_frame, text="Right (-):").grid(row=1, column=2, sticky='w')

        tmargin_spinbox = ttk.Spinbox(layout_frame, from_=0, to=1000, increment=1, textvariable=widgets['tmargin'], width=7, state='disabled', command=plot_callback)
        tmargin_spinbox.grid(row=2, column=1); tmargin_spinbox.bind("<Return>", plot_callback); widgets['tmargin_entry'] = tmargin_spinbox
        ttk.Label(layout_frame, text="Top (-):").grid(row=2, column=0, sticky='w')

        bmargin_spinbox = ttk.Spinbox(layout_frame, from_=0, to=1000, increment=1, textvariable=widgets['bmargin'], width=7, state='disabled', command=plot_callback)
        bmargin_spinbox.grid(row=2, column=3); bmargin_spinbox.bind("<Return>", plot_callback); widgets['bmargin_entry'] = bmargin_spinbox
        ttk.Label(layout_frame, text="Bottom (+):").grid(row=2, column=2, sticky='w')
        
        ttk.Separator(layout_frame).grid(row=3, column=0, columnspan=4, sticky='ew', pady=10)
        widgets['lock_aspect_ratio'] = tk.BooleanVar(value=True)
        ttk.Checkbutton(layout_frame, text="Aspect ratio (height / width):", variable=widgets['lock_aspect_ratio'], command=lambda w=widgets: self.update_aspect_ratio_entry_state(w)).grid(row=4, column=0, columnspan=2, sticky='w')
        widgets['aspect_ratio'] = tk.StringVar(value='0.75')
        widgets['aspect_ratio_entry'] = ttk.Entry(layout_frame, textvariable=widgets['aspect_ratio'], width=8, state='normal')
        widgets['aspect_ratio_entry'].grid(row=4, column=2)
        widgets['aspect_ratio_entry'].bind("<Return>", plot_callback)
        
        # --- Normal Mode Actions ---
        main_action_frame = ttk.Frame(normal_frame); main_action_frame.pack(fill='x', pady=10)
        ttk.Button(main_action_frame, text="Plot / Refresh", command=lambda w=widgets, k=key: self.plot(w, k)).pack(pady=5)
        
        replot_frame = ttk.Frame(normal_frame); replot_frame.pack(fill='x', pady=5)
        widgets['replot_interval'] = tk.StringVar(value='1000'); ttk.Label(replot_frame, text="Auto (ms):").pack(side='left'); ttk.Entry(replot_frame, textvariable=widgets['replot_interval'], width=8).pack(side='left', padx=5); widgets['start_button'] = ttk.Button(replot_frame, text="Start", command=lambda w=widgets, k=key: self.start_replot(w, k)); widgets['start_button'].pack(side='left'); widgets['stop_button'] = ttk.Button(replot_frame, text="Stop", state="disabled", command=lambda w=widgets: self.stop_replot(w)); widgets['stop_button'].pack(side='left', padx=5)

        # --- LOGFILE MODE WIDGETS ---
        logfile_frame = widgets['logfile_mode_frame']

        logfile_selection_frame = ttk.LabelFrame(logfile_frame, text="Logfile Selection", padding=10)
        logfile_selection_frame.pack(fill='x', pady=5)
        widgets['logfile_path'] = tk.StringVar()
        ttk.Label(logfile_selection_frame, text="Logfile:").grid(row=0, column=0, sticky='w')
        ttk.Entry(logfile_selection_frame, textvariable=widgets['logfile_path']).grid(row=0, column=1, sticky='ew')
        ttk.Button(logfile_selection_frame, text="Browse...", command=lambda w=widgets: self._browse_logfile(w)).grid(row=0, column=2, padx=5)
        ttk.Button(logfile_selection_frame, text="Parse Logfile", command=lambda w=widgets, k=key: self._parse_logfile(w, k)).grid(row=1, column=1, pady=5)
        logfile_selection_frame.columnconfigure(1, weight=1)

        # Logfile Layout & Grid
        logfile_layout_frame = ttk.LabelFrame(logfile_frame, text="Layout, Margins & Grid", padding=10)
        logfile_layout_frame.pack(fill='x', pady=5)
        
        # --- Margins ---
        ttk.Label(logfile_layout_frame, text="Margins:").grid(row=0, column=0, sticky='w', rowspan=2)
        
        ttk.Label(logfile_layout_frame, text="Left").grid(row=0, column=1, sticky='s', padx=2)
        ttk.Label(logfile_layout_frame, text="Right").grid(row=0, column=2, sticky='s', padx=2)
        ttk.Label(logfile_layout_frame, text="Bottom").grid(row=0, column=3, sticky='s', padx=2)
        ttk.Label(logfile_layout_frame, text="Top").grid(row=0, column=4, sticky='s', padx=2)

        widgets['logfile_lmargin'] = tk.StringVar(value='0.1'); widgets['logfile_rmargin'] = tk.StringVar(value='0.9'); widgets['logfile_bmargin'] = tk.StringVar(value='0.1'); widgets['logfile_tmargin'] = tk.StringVar(value='0.9')
        
        lmargin_spin = ttk.Spinbox(logfile_layout_frame, from_=0.0, to=1.0, increment=0.05, textvariable=widgets['logfile_lmargin'], width=5, command=plot_callback)
        lmargin_spin.grid(row=1, column=1, padx=2)
        lmargin_spin.bind("<Return>", plot_callback)
        
        rmargin_spin = ttk.Spinbox(logfile_layout_frame, from_=0.0, to=1.0, increment=0.05, textvariable=widgets['logfile_rmargin'], width=5, command=plot_callback)
        rmargin_spin.grid(row=1, column=2, padx=2)
        rmargin_spin.bind("<Return>", plot_callback)

        bmargin_spin = ttk.Spinbox(logfile_layout_frame, from_=0.0, to=1.0, increment=0.05, textvariable=widgets['logfile_bmargin'], width=5, command=plot_callback)
        bmargin_spin.grid(row=1, column=3, padx=2)
        bmargin_spin.bind("<Return>", plot_callback)
        
        tmargin_spin = ttk.Spinbox(logfile_layout_frame, from_=0.0, to=1.0, increment=0.05, textvariable=widgets['logfile_tmargin'], width=5, command=plot_callback)
        tmargin_spin.grid(row=1, column=4, padx=2)
        tmargin_spin.bind("<Return>", plot_callback)

        # --- Spacing ---
        ttk.Label(logfile_layout_frame, text="Spacing (X, Y):").grid(row=2, column=0, sticky='w', pady=(5,2))
        widgets['logfile_xspacing'] = tk.StringVar(value='0.08'); widgets['logfile_yspacing'] = tk.StringVar(value='0.08')

        xspacing_spin = ttk.Spinbox(logfile_layout_frame, from_=0.0, to=1.0, increment=0.01, textvariable=widgets['logfile_xspacing'], width=5, command=plot_callback)
        xspacing_spin.grid(row=2, column=1, padx=2)
        xspacing_spin.bind("<Return>", plot_callback)

        yspacing_spin = ttk.Spinbox(logfile_layout_frame, from_=0.0, to=1.0, increment=0.01, textvariable=widgets['logfile_yspacing'], width=5, command=plot_callback)
        yspacing_spin.grid(row=2, column=2, padx=2)
        yspacing_spin.bind("<Return>", plot_callback)

        # --- Grid ---
        ttk.Label(logfile_layout_frame, text="Grid:").grid(row=3, column=0, sticky='w', pady=2)
        widgets['logfile_grid_on'] = tk.BooleanVar(value=True); widgets['logfile_grid_style'] = tk.StringVar(value='Medium')
        ttk.Checkbutton(logfile_layout_frame, text="On", variable=widgets['logfile_grid_on'], command=plot_callback).grid(row=3, column=1, sticky='w')
        grid_combo = ttk.Combobox(logfile_layout_frame, textvariable=widgets['logfile_grid_style'], values=['Light', 'Medium', 'Dark'], width=8)
        grid_combo.grid(row=3, column=2, columnspan=2, sticky='w')
        grid_combo.bind("<<ComboboxSelected>>", plot_callback)

        # --- Logfile Mode Actions (Moved Up) ---
        logfile_action_frame = ttk.LabelFrame(logfile_frame, text="Plot Actions", padding=10)
        logfile_action_frame.pack(fill='x', pady=5)
        
        logfile_main_action_frame = ttk.Frame(logfile_action_frame); logfile_main_action_frame.pack(pady=2)
        ttk.Button(logfile_main_action_frame, text="Plot / Refresh", command=lambda w=widgets, k=key: self.refresh_and_plot(w, k)).pack()
        
        logfile_replot_frame = ttk.Frame(logfile_action_frame); logfile_replot_frame.pack(pady=2)
        widgets['logfile_replot_interval'] = tk.StringVar(value='1000')
        ttk.Label(logfile_replot_frame, text="Auto (ms):").pack(side='left')
        ttk.Entry(logfile_replot_frame, textvariable=widgets['logfile_replot_interval'], width=8).pack(side='left', padx=5)
        widgets['logfile_start_button'] = ttk.Button(logfile_replot_frame, text="Start", command=lambda w=widgets, k=key: self.start_replot(w, k)); widgets['logfile_start_button'].pack(side='left')
        widgets['logfile_stop_button'] = ttk.Button(logfile_replot_frame, text="Stop", state="disabled", command=lambda w=widgets: self.stop_replot(w)); widgets['logfile_stop_button'].pack(side='left', padx=5)

        subplot_config_frame = ttk.LabelFrame(logfile_frame, text="Sub-plot Configuration", padding=10)
        subplot_config_frame.pack(fill='x', pady=5, expand=True)
        
        widgets['subplot_vars'] = []
        
        for i in range(4):
            title = ["Top-Left", "Top-Right", "Bottom-Left", "Bottom-Right"][i]
            
            sub_frame = ttk.LabelFrame(subplot_config_frame, text=f"Sub-plot {i+1} ({title})", padding=10)
            sub_frame.grid(row=i, column=0, sticky='ew', padx=5, pady=5)
            subplot_config_frame.columnconfigure(0, weight=1)

            # --- Row 0: Labels and Options ---
            top_frame = ttk.Frame(sub_frame); top_frame.pack(fill='x')
            
            y_label_var = tk.StringVar()
            ttk.Label(top_frame, text="Y-Axis Label:").pack(side='left')
            y_label_entry = ttk.Entry(top_frame, textvariable=y_label_var, width=20)
            y_label_entry.pack(side='left', fill='x', expand=True, padx=5)
            y_label_entry.bind("<Return>", plot_callback)
            
            y_log_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(top_frame, text="Y Log", variable=y_log_var, command=plot_callback).pack(side='left', padx=5)

            show_legend_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(top_frame, text="Show Legend", variable=show_legend_var, command=plot_callback).pack(side='left', padx=5)
            
            # --- Row 1 & 2: Axis Ranges ---
            range_frame = ttk.Frame(sub_frame); range_frame.pack(fill='x', pady=5)
            
            def create_range_controls(parent, axis_name, row, plot_cb):
                ttk.Label(parent, text=f"{axis_name}-Axis Range:").grid(row=row, column=0, sticky="w")
                range_mode_var = tk.StringVar(value='auto')
                min_var = tk.StringVar(); max_var = tk.StringVar()
                
                min_entry = ttk.Entry(parent, textvariable=min_var, width=8, state='disabled')
                max_entry = ttk.Entry(parent, textvariable=max_var, width=8, state='disabled')

                def update_state():
                    state = 'normal' if range_mode_var.get() == 'manual' else 'disabled'
                    min_entry.config(state=state)
                    max_entry.config(state=state)
                
                def update_and_plot():
                    update_state()
                    plot_cb()

                ttk.Radiobutton(parent, text="Auto", variable=range_mode_var, value='auto', command=update_and_plot).grid(row=row, column=1, sticky="w")
                ttk.Radiobutton(parent, text="Manual:", variable=range_mode_var, value='manual', command=update_and_plot).grid(row=row, column=2, sticky="w")
                min_entry.grid(row=row, column=3, padx=2)
                ttk.Label(parent, text="to").grid(row=row, column=4, padx=2)
                max_entry.grid(row=row, column=5, padx=2)
                
                min_entry.bind("<Return>", plot_cb)
                max_entry.bind("<Return>", plot_cb)

                return {
                    'mode': range_mode_var, 'min': min_var, 'max': max_var,
                    'min_entry': min_entry, 'max_entry': max_entry
                }

            x_range_vars = create_range_controls(range_frame, 'X', 0, plot_callback)
            y_range_vars = create_range_controls(range_frame, 'Y', 1, plot_callback)

            # --- Row 3: Column Selection Listbox ---
            ttk.Label(sub_frame, text="Y-Axis Columns:").pack(anchor='w', pady=(5,0))
            list_frame = ttk.Frame(sub_frame); list_frame.pack(fill='both', expand=True)
            listbox = tk.Listbox(list_frame, selectmode='multiple', height=5, exportselection=False)
            listbox.pack(side='left', fill='both', expand=True)
            list_scroll = ttk.Scrollbar(list_frame, orient='vertical', command=listbox.yview); list_scroll.pack(side='right', fill='y')
            listbox.config(yscrollcommand=list_scroll.set)
            listbox.bind("<<ListboxSelect>>", plot_callback)

            widgets['subplot_vars'].append({
                'y_label': y_label_var, 
                'y_log': y_log_var,
                'show_legend': show_legend_var,
                'x_range': x_range_vars,
                'y_range': y_range_vars,
                'listbox': listbox
            })
            
        # --- COMMON WIDGETS (Plot Display & Close Tab) ---
        ttk.Separator(controls_frame).pack(fill='x', pady=10)
        ttk.Button(controls_frame, text="Close Tab", command=lambda k=key: self.close_tab(k)).pack()
        
        # --- Plot and Log Viewer Widgets ---
        export_frame = ttk.Frame(plot_image_frame); export_frame.pack(side='bottom', fill='x', pady=5)
        ttk.Button(export_frame, text="Save Plot...", command=lambda w=widgets, k=key: self.save_plot(w, k)).pack(side='left', padx=5)
        ttk.Button(export_frame, text="Copy to Clipboard", command=lambda w=widgets, k=key: self.copy_plot_to_clipboard(w, k)).pack(side='left', padx=5)
        widgets['plot_label'] = ttk.Label(plot_image_frame, text="Plot will appear here...", anchor='center'); widgets['plot_label'].pack(expand=True, fill='both')
        
        log_text = tk.Text(log_viewer_frame, wrap='word', height=10, state='disabled')
        log_scroll = ttk.Scrollbar(log_viewer_frame, orient='vertical', command=log_text.yview)
        log_text.config(yscrollcommand=log_scroll.set)
        log_scroll.pack(side='right', fill='y')
        log_text.pack(side='left', fill='both', expand=True)
        widgets['log_text_widget'] = log_text
        
        tab_data = {
            'widgets': widgets, 
            'plot_width': 600, 
            'plot_height': 400, 
            'resize_job': None, 
            'frame': tab_frame,
            'paned_window': paned_window,
            'plot_display_panedwindow': plot_display_panedwindow,
            'log_viewer_frame': log_viewer_frame,
            'temp_file_path': None,
            'logfile_df': None,
            'parsed_byte_offset': 0,
            'logfile_columns': [],
            'monitored_columns': None, # New: To store user's selection
            'stop_tailing': threading.Event(),
            'tail_thread': None,
            'logfile_monitor_job': None
        }
        plot_image_frame.bind("<Configure>", lambda event, k=key: self.on_plot_resize(event, k))
        self.tabs[key] = tab_data
        
        self._switch_mode(widgets, key) # Set initial view
        return tab_frame

    def _get_selected_or_focused_item(self, tree):
        """Helper to get the ID of the selected or focused item."""
        selected = tree.selection()
        if selected:
            return selected[0]
        return tree.focus() or None

    def _start_log_tail(self, key, filepath):
        self._stop_log_tail(key) # Stop any previous thread
        
        tab_data = self.tabs[key]
        tab_data['stop_tailing'].clear()
        
        thread = threading.Thread(
            target=self._tail_worker, 
            args=(filepath, self.log_queue, key, tab_data['stop_tailing']),
            daemon=True
        )
        thread.start()
        tab_data['tail_thread'] = thread

    def _stop_log_tail(self, key):
        if key in self.tabs:
            tab_data = self.tabs[key]
            if tab_data.get('tail_thread') and tab_data['tail_thread'].is_alive():
                tab_data['stop_tailing'].set()
            if tab_data.get('logfile_monitor_job'):
                self.root.after_cancel(tab_data['logfile_monitor_job'])
                tab_data['logfile_monitor_job'] = None
    
    @staticmethod
    def _tail_worker(filepath, q, key, stop_event):
        try:
            with open(filepath, 'r') as f:
                f.seek(0, 2) # Go to the end of the file
                while not stop_event.is_set():
                    line = f.readline()
                    if line:
                        q.put((key, line))
                    else:
                        time.sleep(0.1) # Wait for new lines
        except Exception as e:
            print(f"Error in tail worker for {key}: {e}")

    def _process_log_queue(self):
        try:
            while not self.log_queue.empty():
                key, line = self.log_queue.get_nowait()
                if key in self.tabs:
                    text_widget = self.tabs[key]['widgets']['log_text_widget']
                    text_widget.config(state='normal')
                    text_widget.insert('end', line)
                    text_widget.see('end')
                    text_widget.config(state='disabled')
        finally:
            self.root.after(100, self._process_log_queue)

    def _browse_logfile(self, widgets):
        filename = filedialog.askopenfilename(title="Select a log file", filetypes=(("Log files", "*.log"), ("All files", "*.*")))
        if filename:
            widgets['logfile_path'].set(filename)

    def _parse_logfile(self, widgets, key):
        logfile_path = widgets['logfile_path'].get()
        if not logfile_path or not os.path.exists(logfile_path):
            messagebox.showwarning("No File", "Please select a valid logfile first.")
            return

        # --- New logic to wait for file to populate ---
        with open(logfile_path, 'r') as f:
            time_blocks = [line for line in f if re.match(r"^\s*Time = (\S+)\s*$", line)]
        
        if len(time_blocks) < 3:
            self._wait_for_logfile_data(widgets, key, logfile_path, 0)
        else:
            self._execute_full_parse(widgets, key, logfile_path)

    def _wait_for_logfile_data(self, widgets, key, filepath, checks_done):
        tab_data = self.tabs[key]
        
        # Stop previous monitor if any
        if tab_data.get('logfile_monitor_job'):
            self.root.after_cancel(tab_data['logfile_monitor_job'])

        if checks_done == 0:
            messagebox.showinfo("Monitoring Logfile", "Logfile has fewer than three 'Time' blocks. Monitoring for changes...")
            tab_data['last_mtime'] = os.path.getmtime(filepath)
            tab_data['stale_time'] = 0

        try:
            current_mtime = os.path.getmtime(filepath)
            if current_mtime == tab_data['last_mtime']:
                tab_data['stale_time'] += 2
                if tab_data['stale_time'] >= 20:
                    messagebox.showwarning("Logfile Stalled", "The logfile has not changed for 20 seconds. Please check your simulation.")
                    return
            else:
                tab_data['last_mtime'] = current_mtime
                tab_data['stale_time'] = 0

            with open(filepath, 'r') as f:
                time_blocks = [line for line in f if re.match(r"^\s*Time = (\S+)\s*$", line)]
            
            if len(time_blocks) >= 3:
                self._execute_full_parse(widgets, key, filepath)
                return

        except FileNotFoundError:
            messagebox.showerror("Error", f"Logfile not found: {filepath}")
            return
        
        # Reschedule check
        tab_data['logfile_monitor_job'] = self.root.after(2000, lambda: self._wait_for_logfile_data(widgets, key, filepath, checks_done + 1))

    def _downcast_dataframe(self, df):
        """Downcast numeric columns of a DataFrame to save memory."""
        for col in df.columns:
            if df[col].dtype == 'float64':
                df[col] = pd.to_numeric(df[col], downcast='float')
            elif df[col].dtype == 'int64':
                df[col] = pd.to_numeric(df[col], downcast='integer')
        return df

    def _execute_full_parse(self, widgets, key, logfile_path, silent=False):
        parser = LogfileParser(logfile_path)
        df, error, byte_offset = parser.parse()
        tab_data = self.tabs[key]

        if error:
            if not silent: messagebox.showerror("Parsing Error", error)
            return False
        
        df = self._downcast_dataframe(df)
        all_columns = [col for col in df.columns if col != 'Time']

        # --- New Column Selection Step ---
        if tab_data.get('monitored_columns') is None:
            dialog = ColumnSelectorDialog(self.root, all_columns)
            monitored_columns = dialog.result
            if monitored_columns is None: # User cancelled
                return False
            tab_data['monitored_columns'] = monitored_columns
        else: # Use columns loaded from session
            monitored_columns = tab_data['monitored_columns']
        
        # Filter the dataframe based on selection
        df = df[['Time'] + [col for col in monitored_columns if col in df.columns]]

        tab_data['logfile_df'] = df
        tab_data['parsed_byte_offset'] = byte_offset

        if tab_data.get('temp_file_path') and os.path.exists(tab_data['temp_file_path']):
            os.remove(tab_data['temp_file_path'])
        
        with NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='') as tmpfile:
            df.to_csv(tmpfile, index=False)
            tab_data['temp_file_path'] = tmpfile.name
        
        residual_cols = sorted([c for c in monitored_columns if 'initial_residual' in c])
        other_cols = sorted([c for c in monitored_columns if 'initial_residual' not in c])
        sorted_cols = residual_cols + other_cols
        
        tab_data['logfile_columns'] = sorted_cols
        
        for i in range(4):
            listbox = widgets['subplot_vars'][i]['listbox']
            selected = [listbox.get(idx) for idx in listbox.curselection()]
            listbox.delete(0, 'end')
            for col in sorted_cols:
                listbox.insert('end', col)
            for item in selected:
                try:
                    idx = sorted_cols.index(item)
                    listbox.selection_set(idx)
                except ValueError:
                    pass

        if not silent:
            self._start_log_tail(key, logfile_path)
            messagebox.showinfo("Success", f"Logfile parsed successfully. Monitoring {len(monitored_columns)} columns.")
        return True

    def _execute_incremental_parse(self, key):
        tab_data = self.tabs[key]
        logfile_path = tab_data['widgets']['logfile_path'].get()

        if not logfile_path or not os.path.exists(logfile_path) or tab_data.get('monitored_columns') is None:
            return False

        try:
            with open(logfile_path, 'r') as f:
                f.seek(tab_data['parsed_byte_offset'])
                new_lines = f.readlines()
                new_offset = f.tell()
            
            if not new_lines:
                return True # Nothing new to parse

            parser = LogfileParser()
            new_records = parser.parse_lines(new_lines, monitored_columns=tab_data['monitored_columns'])

            if not new_records:
                tab_data['parsed_byte_offset'] = new_offset
                return True

            new_df = pd.DataFrame.from_records(new_records)
            new_df = self._downcast_dataframe(new_df)
            
            combined_df = pd.concat([tab_data['logfile_df'], new_df], ignore_index=True)
            combined_df = combined_df.ffill()
            combined_df = combined_df.sort_values(by='Time').drop_duplicates(subset='Time', keep='last')
            
            tab_data['logfile_df'] = combined_df
            tab_data['parsed_byte_offset'] = new_offset

            with open(tab_data['temp_file_path'], 'w', newline='') as tmpfile:
                combined_df.to_csv(tmpfile, index=False)

            return True

        except Exception as e:
            print(f"Error during incremental parse: {e}")
            return False

    def _on_separator_change(self, widgets):
        if widgets['separator'].get() == ',':
            widgets['detect_headers'].set(False)
            widgets['clean_data'].set(False)
            widgets['clean_cb'].config(state='disabled')
            widgets['load_all_button'].config(state='disabled')
        else: # whitespace
            widgets['detect_headers'].set(True)
            widgets['clean_cb'].config(state='normal')
            if widgets['tree'].selection():
                self.on_tree_select(None, widgets)

    def _on_clean_data_toggle(self, widgets):
        if widgets['clean_data'].get():
            widgets['detect_headers'].set(False)
            widgets['detect_headers_cb'].config(state='disabled')
            widgets['load_all_button'].config(state='disabled')
        else:
            widgets['detect_headers'].set(True)
            widgets['detect_headers_cb'].config(state='normal')
            if widgets['tree'].selection():
                widgets['load_all_button'].config(state='normal')

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
    
    def _validate_positive_integer(self, value_str, field_name):
        if not value_str.strip(): return True
        try:
            val = int(value_str)
            if val < 0:
                messagebox.showwarning("Invalid Input", f"'{field_name}' must be a positive number or zero.\nYou entered: '{value_str}'")
                return False
            return True
        except ValueError:
            messagebox.showwarning("Invalid Input", f"Please enter a valid whole number for '{field_name}'.\nYou entered: '{value_str}'")
            return False

    def generate_logfile_plot_script(self, widgets, key, terminal_config):
        tab_data = self.tabs[key]
        temp_file = tab_data.get('temp_file_path')
        if not temp_file or not os.path.exists(temp_file):
            messagebox.showwarning("No Data", "Please parse a logfile before plotting.")
            return None, None

        # Validate margins and spacing
        for var, name in [(widgets['logfile_lmargin'], "Left Margin"), (widgets['logfile_rmargin'], "Right Margin"),
                          (widgets['logfile_bmargin'], "Bottom Margin"), (widgets['logfile_tmargin'], "Top Margin"),
                          (widgets['logfile_xspacing'], "X Spacing"), (widgets['logfile_yspacing'], "Y Spacing")]:
            if not self._validate_numeric(var.get(), name): return None, None
        
        margin_cmd = f"margins {widgets['logfile_lmargin'].get()}, {widgets['logfile_rmargin'].get()}, {widgets['logfile_bmargin'].get()}, {widgets['logfile_tmargin'].get()}"
        spacing_cmd = f"spacing {widgets['logfile_xspacing'].get()}, {widgets['logfile_yspacing'].get()}"

        script = f"""
            set terminal {terminal_config['term']} size {terminal_config['size']} enhanced font 'Verdana,10'
            set output '{terminal_config['output']}'
            set datafile separator ","
            set multiplot layout 2,2 {margin_cmd} {spacing_cmd}
        """
        
        if widgets['logfile_grid_on'].get():
            color_map = {'Light': 'gray80', 'Medium': 'gray50', 'Dark': 'gray20'}
            grid_color = color_map.get(widgets['logfile_grid_style'].get(), 'gray50')
            script += f'set grid back linetype 0 linecolor "{grid_color}"\n'
        else:
            script += 'unset grid\n'

        has_plot = False
        for i in range(4):
            sub_plot_vars = widgets['subplot_vars'][i]
            script += f'\n# Subplot {i+1}\n'
            
            # Key (Legend)
            script += 'set key on\n' if sub_plot_vars['show_legend'].get() else 'set key off\n'

            # Ranges
            x_range = sub_plot_vars['x_range']; y_range = sub_plot_vars['y_range']
            if x_range['mode'].get() == 'manual' and x_range['min'].get() and x_range['max'].get(): script += f"set xrange [{x_range['min'].get()}:{x_range['max'].get()}]\n"
            else: script += "set autoscale x\n"
            if y_range['mode'].get() == 'manual' and y_range['min'].get() and y_range['max'].get(): script += f"set yrange [{y_range['min'].get()}:{y_range['max'].get()}]\n"
            else: script += "set autoscale y\n"

            # Labels and Logscale
            script += f'set xlabel "Time"\n'
            script += f'set ylabel "{sub_plot_vars["y_label"].get()}"\n'
            if sub_plot_vars['y_log'].get(): script += 'set logscale y\n'
            
            # Plot clauses
            listbox = sub_plot_vars['listbox']
            selected_indices = listbox.curselection()
            if selected_indices:
                has_plot = True
                plot_clauses = []
                for index in selected_indices:
                    col_name = listbox.get(index)
                    legend_title = col_name.replace('_', '-')
                    plot_clauses.append(f"'{temp_file}' using \"Time\":\"{col_name}\" with lines title \"{legend_title}\"")
                script += "plot " + ", ".join(plot_clauses) + "\n"
            else: 
                script += "plot [0:1][0:1] -1 with lines notitle\n"

            # Unset settings for next plot
            if sub_plot_vars['y_log'].get(): script += 'unset logscale y\n'

        if not has_plot:
            messagebox.showinfo("Info", "No columns selected for plotting in any sub-plot.")
            return None, None

        script += "\nunset multiplot\nunset grid\nset key on\n"
        return script, None

    def generate_gnuplot_script(self, widgets, key, terminal_config):
        # Validation checks
        if not self._validate_positive_integer(widgets['title_font_size'].get(), "Title Font Size") or \
           not self._validate_positive_integer(widgets['axes_font_size'].get(), "Axes Font Size") or \
           not self._validate_positive_integer(widgets['legend_font_size'].get(), "Legend Font Size"):
            return None, None
        if widgets['x_range_mode'].get() == 'manual':
            if not self._validate_numeric(widgets['x_min'].get(), "X-Axis Min") or not self._validate_numeric(widgets['x_max'].get(), "X-Axis Max"): return None, None
        if widgets['y_range_mode'].get() == 'manual':
            if not self._validate_numeric(widgets['y_min'].get(), "Y1-Axis Min") or not self._validate_numeric(widgets['y_max'].get(), "Y1-Axis Max"): return None, None
        if widgets['y2_range_mode'].get() == 'manual':
            if not self._validate_numeric(widgets['y2_min'].get(), "Y2-Axis Min") or not self._validate_numeric(widgets['y2_max'].get(), "Y2-Axis Max"): return None, None
        if widgets['lock_aspect_ratio'].get():
            if not self._validate_numeric(widgets['aspect_ratio'].get(), "Aspect Ratio"): return None, None
        
        if widgets['use_custom_margins'].get():
            if not self._validate_positive_integer(widgets['lmargin'].get(), "Left Margin") or \
               not self._validate_positive_integer(widgets['rmargin'].get(), "Right Margin") or \
               not self._validate_positive_integer(widgets['tmargin'].get(), "Top Margin") or \
               not self._validate_positive_integer(widgets['bmargin'].get(), "Bottom Margin"):
                return None, None

        separator = widgets['separator'].get()
        detect_headers = widgets['detect_headers'].get()
        
        separator_settings = ""
        key_settings = f"set key font ',{widgets['legend_font_size'].get()}'"
        use_explicit_titles = True

        if separator == ',':
            separator_settings = 'set datafile separator ","'
            if detect_headers:
                key_settings += '\nset key autotitle columnheader'
                use_explicit_titles = False
        
        global_title = widgets['plot_global_title'].get()
        title_font = widgets['title_font_size'].get()
        title_settings = f'set title "{global_title}" font ",{title_font}"' if global_title else 'unset title'
        
        axes_font = widgets['axes_font_size'].get()
        
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

            title_part = f"title '{values[5]}'" if use_explicit_titles else ""
            clause = f"{plot_source} using {values[1]}:{values[2]} with {values[4]} {title_part}"
            
            if values[3] == 'Y2': y2_clauses.append(clause + " axes x1y2")
            else: y1_clauses.append(clause + " axes x1y1")

        if not y1_clauses and not y2_clauses: return None, None
        full_plot_command = "plot " + ", ".join(y1_clauses + y2_clauses)
        y2_settings = ""
        if y2_clauses:
            y2_settings += "set ytics nomirror\nset y2tics\n"
            y2_settings += f'set y2label "{widgets["y2label"].get()}" font ",{axes_font}"\n'
            y2_settings += f'set y2tics font ",{axes_font}"\n'
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
            {separator_settings}
            {key_settings}
            {title_settings}
            {margin_settings}
            {aspect_ratio_settings}
            set xlabel "{widgets['xlabel'].get()}" font ",{axes_font}"
            set ylabel "{widgets['ylabel'].get()}" font ",{axes_font}"
            set xtics font ",{axes_font}"
            set ytics font ",{axes_font}"
            {log_settings}
            {grid_settings}
            {range_settings}
            {y2_settings}
            {full_plot_command}
            set datafile separator whitespace
            unset key
            unset output
        """
        return script, data_to_pipe

    def refresh_and_plot(self, widgets, key):
        """Used by the manual refresh button in logfile mode."""
        self._execute_incremental_parse(key)
        self.plot(widgets, key)

    def plot(self, widgets, key):
        width, height = self.tabs[key]['plot_width'], self.tabs[key]['plot_height']
        image_filename = f"plot_{key}.png"
        terminal_config = {'term': 'pngcairo', 'size': f'{width},{height}', 'output': image_filename}
        
        mode = widgets['mode'].get()
        gnuplot_script, data_to_pipe = None, None

        if mode == "Normal":
            gnuplot_script, data_to_pipe = self.generate_gnuplot_script(widgets, key, terminal_config)
        else: # Plot Logfile
            gnuplot_script, data_to_pipe = self.generate_logfile_plot_script(widgets, key, terminal_config)
        
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

        mode = widgets['mode'].get()
        gnuplot_script, data_to_pipe = None, None

        if mode == "Normal":
            gnuplot_script, data_to_pipe = self.generate_gnuplot_script(widgets, key, terminal_config)
        else: # Plot Logfile
            gnuplot_script, data_to_pipe = self.generate_logfile_plot_script(widgets, key, terminal_config)

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
        
        mode = widgets['mode'].get()
        gnuplot_script, data_to_pipe = None, None
        
        if mode == "Normal":
            gnuplot_script, data_to_pipe = self.generate_gnuplot_script(widgets, key, terminal_config)
        else: # Plot Logfile
            gnuplot_script, data_to_pipe = self.generate_logfile_plot_script(widgets, key, terminal_config)

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

    def _get_column_count(self, filepath):
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line.startswith("# Time"):
                        return len(stripped_line.lstrip('#').split())
        except Exception:
            return 0
        return 0

    def add_dataset(self, widgets, key):
        filepath = widgets['filepath'].get()
        if not filepath: return
        
        plot_title_to_set = widgets['plot_title'].get()

        if widgets['detect_headers'].get() and widgets['separator'].get() == 'whitespace':
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
        selected_item = self._get_selected_or_focused_item(widgets['tree'])
        if not selected_item:
            messagebox.showinfo("Info", "Please select a dataset to duplicate.")
            return
        
        values = list(widgets['tree'].item(selected_item, "values"))
        full_path = widgets['tree'].item(selected_item, "tags")[0]

        try:
            original_y_col = int(values[2])
            new_y_col = original_y_col + 1
            values[2] = str(new_y_col)

            plot_title_to_set = ""
            if widgets['detect_headers'].get() and widgets['separator'].get() == 'whitespace':
                header_title = self._get_column_header(full_path, new_y_col)
                if header_title:
                    plot_title_to_set = header_title
            
            if not plot_title_to_set:
                original_title = values[5]
                base_title = original_title.split(' (col')[0]
                plot_title_to_set = f"{base_title} (col {new_y_col})"
            
            values[5] = plot_title_to_set

            current_tags = widgets['tree'].item(selected_item, 'tags')
            new_tags = (full_path, 'checked')
            if 'load_all_group' in current_tags:
                new_tags += ('load_all_group',)

            widgets['tree'].insert('', 'end', values=tuple(values), tags=new_tags, text="☑")
            self.plot(widgets, key)

        except ValueError:
            messagebox.showerror("Error", f"Could not increment Y-column '{values[2]}'.")

    def load_all_columns(self, widgets, key):
        selected_item = self._get_selected_or_focused_item(widgets['tree'])
        if not selected_item: 
            messagebox.showinfo("Info", "Please select a dataset first.")
            return

        values = list(widgets['tree'].item(selected_item, "values"))
        full_path = widgets['tree'].item(selected_item, "tags")[0]

        total_cols = self._get_column_count(full_path)
        if total_cols <= 2:
            messagebox.showinfo("Info", "Not enough columns detected in the file to load more datasets.")
            return
        
        try:
            start_y_col = int(values[2])
        except ValueError:
            messagebox.showerror("Error", f"The starting Y-column '{values[2]}' is not a valid number.")
            return

        widgets['load_all_button'].config(state='disabled')
        widgets['clean_cb'].config(state='disabled')
        
        widgets['tree'].item(selected_item, tags=(full_path, 'checked', 'load_all_group'))

        for new_y_col in range(start_y_col + 1, total_cols + 1):
            new_values = list(values)
            new_values[2] = str(new_y_col)

            plot_title_to_set = ""
            if widgets['detect_headers'].get() and widgets['separator'].get() == 'whitespace':
                header_title = self._get_column_header(full_path, new_y_col)
                if header_title:
                    plot_title_to_set = header_title
            
            if not plot_title_to_set:
                base_title = new_values[5].split(' (col')[0]
                plot_title_to_set = f"{base_title} (col {new_y_col})"
            
            new_values[5] = plot_title_to_set
            widgets['tree'].insert('', 'end', values=tuple(new_values), tags=(full_path, 'checked', 'load_all_group'), text="☑")

        self.plot(widgets, key)
        
    def update_dataset(self, widgets, key):
        selected_item = self._get_selected_or_focused_item(widgets['tree'])
        if not selected_item: return
        filepath = widgets['filepath'].get()

        plot_title_to_set = widgets['plot_title'].get()

        if widgets['detect_headers'].get() and widgets['separator'].get() == 'whitespace':
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
        current_tags = list(widgets['tree'].item(selected_item, 'tags'))
        
        if 'load_all_group' in current_tags:
            tags_to_set = (filepath, 'checked' if 'checked' in current_tags else 'unchecked', 'load_all_group')
        else:
            tags_to_set = (filepath, 'checked' if 'checked' in current_tags else 'unchecked')

        widgets['tree'].item(selected_item, values=values, tags=tags_to_set)
        widgets['update_button'].config(state='disabled'); widgets['duplicate_button'].config(state='disabled'); widgets['load_all_button'].config(state='disabled'); widgets['remove_button'].config(state='disabled')
        self.plot(widgets, key)

    def remove_dataset(self, widgets, key):
        selected_item = self._get_selected_or_focused_item(widgets['tree'])
        if selected_item: 
            widgets['tree'].delete(selected_item)
            widgets['update_button'].config(state='disabled'); widgets['duplicate_button'].config(state='disabled'); widgets['load_all_button'].config(state='disabled'); widgets['remove_button'].config(state='disabled')
            self.plot(widgets, key)

    def on_tree_select(self, event, widgets):
        selected_items = widgets['tree'].selection()
        if not selected_items: 
            widgets['update_button'].config(state='disabled'); widgets['duplicate_button'].config(state='disabled'); widgets['load_all_button'].config(state='disabled'); widgets['remove_button'].config(state='disabled')
            return

        widgets['update_button'].config(state='normal'); widgets['duplicate_button'].config(state='normal'); widgets['remove_button'].config(state='normal')
        
        selected_item = selected_items[0]
        values = widgets['tree'].item(selected_item, "values"); full_path = widgets['tree'].item(selected_item, "tags")[0]
        tags = widgets['tree'].item(selected_item, "tags")

        widgets['filepath'].set(full_path); widgets['x_col'].set(values[1]); widgets['y_col'].set(values[2]); widgets['y_axis_select'].set(values[3]); widgets['plot_style'].set(values[4]); widgets['plot_title'].set(values[5])
        
        is_clean = (values[6] == 'Yes')
        widgets['clean_data'].set(is_clean)

        is_part_of_load_all = 'load_all_group' in tags

        if is_clean or is_part_of_load_all:
            widgets['load_all_button'].config(state='disabled')
        else:
            widgets['load_all_button'].config(state='normal')
            
        if is_part_of_load_all:
            widgets['clean_cb'].config(state='disabled')
        else:
            widgets['clean_cb'].config(state='normal')
            self._on_clean_data_toggle(widgets)
        
    def start_replot(self, widgets, key):
        mode = widgets['mode'].get()
        if mode == 'Normal':
            start_button = widgets['start_button']
            stop_button = widgets['stop_button']
        else:
            start_button = widgets['logfile_start_button']
            stop_button = widgets['logfile_stop_button']

        self.stop_replot(widgets)
        self.auto_replotting = True
        start_button.config(state="disabled")
        stop_button.config(state="normal")
        self.auto_replot_loop(widgets, key)

    def stop_replot(self, widgets):
        mode = widgets['mode'].get()
        if mode == 'Normal':
            start_button = widgets['start_button']
            stop_button = widgets['stop_button']
        else:
            start_button = widgets['logfile_start_button']
            stop_button = widgets['logfile_stop_button']

        self.auto_replotting = False
        if self.active_auto_replot_job: 
            self.root.after_cancel(self.active_auto_replot_job)
            self.active_auto_replot_job = None
        
        start_button.config(state="normal")
        stop_button.config(state="disabled")

    def auto_replot_loop(self, widgets, key):
        if self.auto_replotting:
            mode = widgets['mode'].get()
            
            # If in logfile mode, incrementally parse before plotting
            if mode == "Plot Logfile":
                if not self._execute_incremental_parse(key):
                    # Stop replotting if silent parse fails (e.g., file deleted)
                    self.stop_replot(widgets)
                    return
            
            self.plot(widgets, key)
            
            try: 
                interval_var = widgets['replot_interval'] if mode == 'Normal' else widgets['logfile_replot_interval']
                interval = int(interval_var.get())

                if interval <= 0:
                    messagebox.showwarning("Invalid Interval", "Auto-replot interval must be a positive number.")
                    self.stop_replot(widgets)
                    return
                self.active_auto_replot_job = self.root.after(interval, lambda: self.auto_replot_loop(widgets, key))
            except ValueError: 
                messagebox.showwarning("Invalid Interval", "Please enter a valid whole number for the auto-replot interval (in ms).")
                self.stop_replot(widgets)

    def save_session(self):
        filepath = filedialog.asksaveasfilename(
            title="Save Session As...",
            filetypes=(("Gnuplot GUI Session", "*.json"), ("All files", "*.*")),
            defaultextension=".json")
        if not filepath:
            return False

        session_data = {'tabs': []}
        
        for tab_id in self.notebook.tabs():
            if self.notebook.tab(tab_id, "text") == '+':
                continue
            
            key_found = None
            for k, v in self.tabs.items():
                if str(v['frame']) == str(tab_id):
                    key_found = k
                    break
            if not key_found:
                continue
            
            tab_info = self.tabs[key_found]
            widgets = tab_info['widgets']
            paned_window = tab_info['paned_window']
            
            plot_sash_pos = 0
            try:
                plot_sash_pos = tab_info['plot_display_panedwindow'].sashpos(0)
            except (tk.TclError, KeyError): # Handle case where sash might not exist
                plot_sash_pos = int(paned_window.winfo_height() * 0.8)

            tab_data = {
                'tab_title': self.notebook.tab(tab_id, 'text'), 
                'sash_position': paned_window.sashpos(0),
                'plot_sash_position': plot_sash_pos,
                'mode': widgets['mode'].get(),
                'settings': {}, 
                'datasets': [],
                'logfile_settings': {
                    'path': widgets['logfile_path'].get(),
                    'monitored_columns': tab_info.get('monitored_columns'),
                    'subplot_y_labels': [v['y_label'].get() for v in widgets['subplot_vars']],
                    'subplot_y_logs': [v['y_log'].get() for v in widgets['subplot_vars']],
                    'subplot_show_legends': [v['show_legend'].get() for v in widgets['subplot_vars']],
                    'subplot_x_ranges': [{'mode': v['x_range']['mode'].get(), 'min': v['x_range']['min'].get(), 'max': v['x_range']['max'].get()} for v in widgets['subplot_vars']],
                    'subplot_y_ranges': [{'mode': v['y_range']['mode'].get(), 'min': v['y_range']['min'].get(), 'max': v['y_range']['max'].get()} for v in widgets['subplot_vars']],
                    'subplot_selections': [v['listbox'].curselection() for v in widgets['subplot_vars']],
                    'margins': [
                        widgets['logfile_lmargin'].get(), widgets['logfile_rmargin'].get(),
                        widgets['logfile_bmargin'].get(), widgets['logfile_tmargin'].get()
                    ],
                    'spacing': [widgets['logfile_xspacing'].get(), widgets['logfile_yspacing'].get()],
                    'grid_on': widgets['logfile_grid_on'].get(),
                    'grid_style': widgets['logfile_grid_style'].get()
                }
            }
            
            # Save Normal mode settings
            for widget_key, var in widgets.items():
                if isinstance(var, (tk.StringVar, tk.BooleanVar)):
                    tab_data['settings'][widget_key] = var.get()

            for item_id in widgets['tree'].get_children():
                item = widgets['tree'].item(item_id)
                dataset_info = {
                    'values': item['values'],
                    'tags': item['tags'],
                    'visible': 'checked' in item['tags']
                }
                tab_data['datasets'].append(dataset_info)
            
            session_data['tabs'].append(tab_data)

        try:
            with open(filepath, 'w') as f:
                json.dump(session_data, f, indent=4)
            messagebox.showinfo("Success", f"Session saved to:\n{filepath}")
            return True
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save session file.\nError: {e}")
            return False

    def load_session(self):
        filepath = filedialog.askopenfilename(
            title="Load Session",
            filetypes=(("Gnuplot GUI Session", "*.json"), ("All files", "*.*")))
        if not filepath:
            return

        try:
            with open(filepath, 'r') as f:
                session_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load or parse session file.\nError: {e}")
            return
            
        for tab_id in self.notebook.tabs():
            if self.notebook.tab(tab_id, "text") != '+':
                self.notebook.forget(tab_id)
        self.tabs.clear()
        self.tab_counter = 0

        if not session_data.get('tabs'):
             self.add_new_tab()
             return

        for i, tab_data in enumerate(session_data['tabs']):
            new_key = self.add_new_tab()
            tab_info = self.tabs[new_key]
            widgets = tab_info['widgets']
            
            self.notebook.tab(i, text=tab_data.get('tab_title', f"Plot {i+1}"))
            
            # Restore Normal mode settings
            settings = tab_data.get('settings', {})
            for key, value in settings.items():
                if key in widgets and isinstance(widgets[key], (tk.StringVar, tk.BooleanVar)):
                    widgets[key].set(value)
            
            datasets = tab_data.get('datasets', [])
            for ds in datasets:
                tags = tuple(ds.get('tags', []))
                text = "☑" if ds.get('visible', True) else "☐"
                widgets['tree'].insert('', 'end', values=ds.get('values', []), tags=tags, text=text)

            # Restore Logfile mode settings
            logfile_settings = tab_data.get('logfile_settings', {})
            if logfile_settings:
                widgets['logfile_path'].set(logfile_settings.get('path', ''))
                tab_info['monitored_columns'] = logfile_settings.get('monitored_columns')

                subplot_y_labels = logfile_settings.get('subplot_y_labels', [])
                subplot_y_logs = logfile_settings.get('subplot_y_logs', [])
                subplot_show_legends = logfile_settings.get('subplot_show_legends', [])
                subplot_x_ranges = logfile_settings.get('subplot_x_ranges', [])
                subplot_y_ranges = logfile_settings.get('subplot_y_ranges', [])

                for j in range(4):
                    # Restore labels and checkboxes
                    if j < len(subplot_y_labels): widgets['subplot_vars'][j]['y_label'].set(subplot_y_labels[j])
                    if j < len(subplot_y_logs): widgets['subplot_vars'][j]['y_log'].set(subplot_y_logs[j])
                    if j < len(subplot_show_legends): widgets['subplot_vars'][j]['show_legend'].set(subplot_show_legends[j])
                    
                    # Restore X-axis range settings and UI state
                    if j < len(subplot_x_ranges):
                        x_range_data = subplot_x_ranges[j]
                        x_range_vars = widgets['subplot_vars'][j]['x_range']
                        x_range_vars['mode'].set(x_range_data.get('mode', 'auto'))
                        x_range_vars['min'].set(x_range_data.get('min', ''))
                        x_range_vars['max'].set(x_range_data.get('max', ''))
                        state = 'normal' if x_range_vars['mode'].get() == 'manual' else 'disabled'
                        x_range_vars['min_entry'].config(state=state)
                        x_range_vars['max_entry'].config(state=state)

                    # Restore Y-axis range settings and UI state
                    if j < len(subplot_y_ranges):
                        y_range_data = subplot_y_ranges[j]
                        y_range_vars = widgets['subplot_vars'][j]['y_range']
                        y_range_vars['mode'].set(y_range_data.get('mode', 'auto'))
                        y_range_vars['min'].set(y_range_data.get('min', ''))
                        y_range_vars['max'].set(y_range_data.get('max', ''))
                        state = 'normal' if y_range_vars['mode'].get() == 'manual' else 'disabled'
                        y_range_vars['min_entry'].config(state=state)
                        y_range_vars['max_entry'].config(state=state)


                margins = logfile_settings.get('margins', ['0.1', '0.9', '0.1', '0.9'])
                widgets['logfile_lmargin'].set(margins[0]); widgets['logfile_rmargin'].set(margins[1])
                widgets['logfile_bmargin'].set(margins[2]); widgets['logfile_tmargin'].set(margins[3])
                
                spacing = logfile_settings.get('spacing', ['0.08', '0.08'])
                widgets['logfile_xspacing'].set(spacing[0]); widgets['logfile_yspacing'].set(spacing[1])

                widgets['logfile_grid_on'].set(logfile_settings.get('grid_on', True))
                widgets['logfile_grid_style'].set(logfile_settings.get('grid_style', 'Medium'))
                
                if widgets['logfile_path'].get():
                    if self._execute_full_parse(widgets, new_key, widgets['logfile_path'].get(), silent=True):
                        subplot_selections = logfile_settings.get('subplot_selections', [])
                        for j, sel in enumerate(subplot_selections):
                            if j < 4:
                                for index in sel:
                                    widgets['subplot_vars'][j]['listbox'].selection_set(index)

            mode = tab_data.get('mode', "Normal")
            widgets['mode'].set(mode)
            self._switch_mode(widgets, new_key)
            
            self._on_separator_change(widgets)
            self.update_range_entry_state(widgets)
            self.update_margin_entry_state(widgets)
            self.update_aspect_ratio_entry_state(widgets)
            self.plot(widgets, new_key)
            
            self.root.update_idletasks()
            sash_pos = tab_data.get('sash_position')
            if sash_pos:
                tab_info['paned_window'].sashpos(0, sash_pos)
            
            plot_sash_pos = tab_data.get('plot_sash_position')
            if plot_sash_pos:
                # Only set sashpos if there are multiple panes (i.e., the sash exists)
                if len(tab_info['plot_display_panedwindow'].panes()) > 1:
                    tab_info['plot_display_panedwindow'].sashpos(0, plot_sash_pos)


        self.notebook.select(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = GnuplotApp(root)
    root.mainloop()


