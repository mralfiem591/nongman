import ctypes
import errno
import io
import os
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import webbrowser
import av
import requests

SFH_SONG_URL = "https://api.songfilehub.com/song/{song_id}"
SFH_SONGS_QUERY_URL = "https://api.songfilehub.com/songs?songID={song_id}"
SFH_AUDIO_URL = "https://api.songfilehub.com/audio/{hash_id}"
SFH_BUCKET_URL = "https://bucket.gdmobilewrlist.com/api/bucket/file/{hash_id}"
NEWGROUNDS_DOWNLOAD_URL = "https://www.newgrounds.com/audio/download/{song_id}"

GH_REPO_URL = "https://github.com/mralfiem591/nongman"
GH_API_LATEST_RELEASE = "https://api.github.com/repos/mralfiem591/nongman/releases/latest"

VERSION = "1.2.0"

DEFAULT_HEADERS = {
    "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) NONGman/{VERSION} {GH_REPO_URL}"
}

def get_gd_directory() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = os.path.expanduser("~/AppData/Local")
    gd_dir = os.path.join(local_app_data, "GeometryDash")
    os.makedirs(gd_dir, exist_ok=True)
    return gd_dir


def ensure_mp3_bytes(audio_bytes: bytes) -> bytes:
    """
    Ensures that audio_bytes is in MP3 format.
    If already MP3, returns it directly without re-encoding.
    If it is WAV, OGG, or another format, converts it to 320kbps MP3 using PyAV.
    """
    if not audio_bytes:
        return audio_bytes

    # Check if already MP3 (ID3 header or MPEG sync word)
    if audio_bytes[:3] == b"ID3" or (len(audio_bytes) > 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
        return audio_bytes

    if b"<!DOCTYPE" in audio_bytes[:50].upper() or b"<HTML" in audio_bytes[:50].upper():
        raise ValueError("Invalid audio data (received HTML web page).")

    try:
        inp = io.BytesIO(audio_bytes)
        in_c = av.open(inp)
        out_io = io.BytesIO()
        out_c = av.open(out_io, mode="w", format="mp3")

        audio_stream = None
        for stream in in_c.streams:
            if stream.type == "audio":
                audio_stream = stream
                break

        if not audio_stream:
            in_c.close()
            out_c.close()
            raise ValueError("No audio stream found in source audio.")

        rate = audio_stream.rate or 44100
        out_s = out_c.add_stream("libmp3lame", rate=rate)
        out_s.bit_rate = 320000

        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=rate)
        for frame in in_c.decode(audio_stream):
            for r_frame in resampler.resample(frame):
                for pkt in out_s.encode(r_frame):
                    out_c.mux(pkt)
        for r_frame in resampler.resample(None):
            for pkt in out_s.encode(r_frame):
                out_c.mux(pkt)
        for pkt in out_s.encode(None):
            out_c.mux(pkt)

        out_c.close()
        in_c.close()
        return out_io.getvalue()
    except Exception as e:
        raise ValueError(f"Failed to convert audio to MP3: {e}")


class NongManagerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"NONGman by mralfiem591 ({VERSION})")
        self.root.geometry("860x520")
        self.root.minsize(700, 400)

        self.current_song_id = ""
        self.all_records = []  # List of dicts representing all fetched results
        self.items_data = {}   # tree item id -> metadata dict for currently visible items

        self.is_playing_preview = False
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        threading.Thread(target=self._check_for_updates, daemon=True).start()

    def _build_ui(self):
        # 1. Top input bar
        top_frame = ttk.Frame(self.root, padding=(10, 10, 10, 6))
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="Song ID:").pack(side=tk.LEFT, padx=(0, 5))

        self.song_id_var = tk.StringVar()
        self.song_id_entry = ttk.Entry(top_frame, textvariable=self.song_id_var, width=16)
        self.song_id_entry.pack(side=tk.LEFT, padx=(0, 8))
        self.song_id_entry.focus()
        self.song_id_entry.bind("<Return>", lambda event: self.on_search())

        self.search_btn = ttk.Button(top_frame, text="Search", command=self.on_search)
        self.search_btn.pack(side=tk.LEFT, padx=(0, 12))

        self.status_var = tk.StringVar(value="Enter a Song ID and click Search.")
        self.status_label = ttk.Label(top_frame, textvariable=self.status_var)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 2. Controls bar: Filter, Type filter, Sort By, Asc/Desc toggle
        controls_frame = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        controls_frame.pack(fill=tk.X)

        # Text Filter (filters Name, Author, Level ID, and Hash)
        ttk.Label(controls_frame, text="Filter:").pack(side=tk.LEFT, padx=(0, 4))
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(controls_frame, textvariable=self.filter_var, width=16)
        self.filter_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.filter_var.trace_add("write", lambda *args: self._apply_filter())

        self.clear_filter_btn = ttk.Button(controls_frame, text="✕", command=self.clear_filter, width=3)
        self.clear_filter_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Type / State Combobox (unrated, mashup, original, etc.)
        ttk.Label(controls_frame, text="Type:").pack(side=tk.LEFT, padx=(0, 4))
        self.type_filter_var = tk.StringVar(value="All")
        self.type_filter_cb = ttk.Combobox(
            controls_frame,
            textvariable=self.type_filter_var,
            state="readonly",
            width=12,
            values=["All"],
        )
        self.type_filter_cb.pack(side=tk.LEFT, padx=(0, 10))
        self.type_filter_cb.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        # Sort By Combobox (Name, Author, Type, DLs, LevelID, Hash)
        ttk.Label(controls_frame, text="Sort By:").pack(side=tk.LEFT, padx=(0, 4))
        self.sort_by_var = tk.StringVar(value="Name")
        self.sort_by_cb = ttk.Combobox(
            controls_frame,
            textvariable=self.sort_by_var,
            state="readonly",
            width=8,
            values=["Name", "Author", "Type", "DLs", "LevelID", "Hash"],
        )
        self.sort_by_cb.pack(side=tk.LEFT, padx=(0, 4))
        self.sort_by_cb.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        # Ascending / Descending button (A or D)
        self.sort_dir_var = tk.StringVar(value="A")
        self.sort_dir_btn = ttk.Button(
            controls_frame,
            textvariable=self.sort_dir_var,
            width=3,
            command=self.toggle_sort_dir,
        )
        self.sort_dir_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Filter count label
        self.filter_count_var = tk.StringVar()
        self.filter_count_label = ttk.Label(controls_frame, textvariable=self.filter_count_var)
        self.filter_count_label.pack(side=tk.LEFT)

        # 3. Table / Treeview frame
        table_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("source", "name", "author", "dls", "type", "level_id", "format", "hash")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("source", text="Source", anchor=tk.CENTER)
        self.tree.heading("name", text="Track Name", anchor=tk.W)
        self.tree.heading("author", text="Author / Creator", anchor=tk.W)
        self.tree.heading("dls", text="DLs", anchor=tk.CENTER)
        self.tree.heading("type", text="Type", anchor=tk.CENTER)
        self.tree.heading("level_id", text="Level ID", anchor=tk.CENTER)
        self.tree.heading("format", text="Format", anchor=tk.CENTER)
        self.tree.heading("hash", text="Audio Hash", anchor=tk.W)

        self.tree.column("source", width=75, minwidth=65, stretch=False, anchor=tk.CENTER)
        self.tree.column("name", width=240, minwidth=140)
        self.tree.column("author", width=130, minwidth=90)
        self.tree.column("dls", width=65, minwidth=50, stretch=False, anchor=tk.CENTER)
        self.tree.column("type", width=75, minwidth=60, stretch=False, anchor=tk.CENTER)
        self.tree.column("level_id", width=80, minwidth=60, stretch=False, anchor=tk.CENTER)
        self.tree.column("format", width=85, minwidth=65, stretch=False, anchor=tk.CENTER)
        self.tree.column("hash", width=90, minwidth=70)

        # Style map and tag configuration for green highlight
        style = ttk.Style()
        def fixed_map(option):
            return [elm for elm in style.map("Treeview", query_opt=option) if elm[:2] != ("!disabled", "!selected")]
        style.map("Treeview", foreground=fixed_map("foreground"), background=fixed_map("background"))

        self.tree.tag_configure(
            "green_highlight",
            foreground="#157347",
            font=("TkDefaultFont", 9, "bold"),
        )

        # Scrollbars
        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # Repo & Update info bar (below table, above selected label)
        repo_frame = ttk.Frame(self.root, padding=(10, 0, 10, 2))
        repo_frame.pack(fill=tk.X)

        self.repo_label = ttk.Label(
            repo_frame,
            text=f"NONGman ({VERSION}) by mralfiem591. GitHub: {GH_REPO_URL}",
            cursor="hand2",
            font=("TkDefaultFont", 10),
        )
        self.repo_label.pack(side=tk.LEFT)
        self.repo_label.bind(
            "<Button-1>",
            lambda e: webbrowser.open_new_tab(GH_REPO_URL),
        )

        self.update_label = tk.Label(
            repo_frame,
            text="",
            bg="#fff3cd",
            fg="#856404",
            cursor="hand2",
            font=("TkDefaultFont", 8, "bold"),
            padx=5,
            pady=1,
            relief="groove",
            bd=1,
        )
        self.update_label.bind(
            "<Button-1>",
            lambda e: webbrowser.open_new_tab(f"{GH_REPO_URL}/releases"),
        )

        # 4. Bottom action bar
        bottom_frame = ttk.Frame(self.root, padding=(10, 1, 10, 2))
        bottom_frame.pack(fill=tk.X)

        self.pull_btn = ttk.Button(bottom_frame, text="Pull", command=self.on_pull)
        self.pull_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.pull_url_btn = ttk.Button(bottom_frame, text="Pull from URL...", command=self.on_pull_from_url)
        self.pull_url_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.select_file_btn = ttk.Button(bottom_frame, text="Select Local File...", command=self.on_select_local_file)
        self.select_file_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.preview_btn = ttk.Button(bottom_frame, text="Preview", command=self.on_preview)
        self.preview_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.selected_var = tk.StringVar(value="No track selected.")
        self.selected_label = ttk.Label(bottom_frame, textvariable=self.selected_var)
        self.selected_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda event: self.on_pull())

        # Right-click context menu for rows in table
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Preview", command=self.on_preview)
        self.context_menu.add_command(label="Preview (Ext.)", command=self.on_preview_external)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Pull", command=self.on_pull)
        self.tree.bind("<Button-3>", self._on_tree_right_click)

    def _set_ui_busy(self, busy: bool):
        state = tk.DISABLED if busy else tk.NORMAL
        self.search_btn.config(state=state)
        self.pull_btn.config(state=state)
        self.preview_btn.config(state=state)
        self.pull_url_btn.config(state=state)
        self.select_file_btn.config(state=state)
        self.song_id_entry.config(state=state)
        self.filter_entry.config(state=state)
        self.clear_filter_btn.config(state=state)
        self.type_filter_cb.config(state="disabled" if busy else "readonly")
        self.sort_by_cb.config(state="disabled" if busy else "readonly")
        self.sort_dir_btn.config(state=state)

    def clear_filter(self):
        self.filter_var.set("")
        self.filter_entry.focus()

    def toggle_sort_dir(self):
        current = self.sort_dir_var.get()
        new_dir = "D" if current == "A" else "A"
        self.sort_dir_var.set(new_dir)
        self._apply_filter()

    def _apply_filter(self):
        query = self.filter_var.get().strip().lower()
        selected_type = self.type_filter_var.get()

        # Clear visible tree items
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.items_data.clear()

        # 1. Filter records
        filtered_records = []
        for record in self.all_records:
            # Filter by Type/State (unrated, mashup, etc.)
            if selected_type and selected_type != "All":
                if record["state"].lower() != selected_type.lower():
                    continue

            # Text filter: search by Track Name, Author/Creator, Level ID, and Audio Hash
            # (do NOT search Source, DLs, Type, or Format)
            if query:
                name_match = query in record["name"].lower()
                author_match = query in record["author"].lower()
                level_id_val = record.get("level_id", "")
                level_match = bool(level_id_val and level_id_val != "-" and query in str(level_id_val).lower())
                hash_match = query in record.get("hash", "").lower()
                if not (name_match or author_match or level_match or hash_match):
                    continue

            filtered_records.append(record)

        # 2. Sort records
        sort_field_map = {
            "name": "name",
            "author": "author",
            "type": "state",
            "dls": "dls",
            "levelid": "level_id",
            "hash": "hash",
        }
        raw_sort = self.sort_by_var.get().strip().lower()
        sort_field = sort_field_map.get(raw_sort, "name")
        reverse = (self.sort_dir_var.get() == "D")

        # Three groups:
        # 1. Original Newgrounds track (always at the very top)
        # 2. Intended NONGs (state is 'rated' or 'unrated') -> placed immediately below NG entry
        # 3. Other NONGs (state not in 'rated', 'unrated') -> placed below intended NONGs
        originals = [r for r in filtered_records if r["is_original"]]
        intended_nongs = [r for r in filtered_records if not r["is_original"] and r.get("state", "").lower() in ("rated", "unrated")]
        other_nongs = [r for r in filtered_records if not r["is_original"] and r.get("state", "").lower() not in ("rated", "unrated")]

        def get_sort_key(r):
            if sort_field == "dls":
                raw_dls = r.get("raw_dls")
                if raw_dls is not None:
                    try:
                        return (0, int(raw_dls))
                    except (ValueError, TypeError):
                        pass
                return (1, -1)
            elif sort_field == "level_id":
                val = r.get("level_id") or ""
                try:
                    return (0, int(val))
                except (ValueError, TypeError):
                    return (1, str(val).lower())
            else:
                val = r.get(sort_field) or ""
                return (0, str(val).lower())

        intended_nongs.sort(key=get_sort_key, reverse=reverse)
        other_nongs.sort(key=get_sort_key, reverse=reverse)

        display_records = originals + intended_nongs + other_nongs

        # 3. Populate Treeview
        for record in display_records:
            is_green = record["is_original"] or (record.get("state", "").lower() in ("rated", "unrated"))
            tags = ("green_highlight",) if is_green else ()

            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    record["source"],
                    record["name"],
                    record["author"],
                    record["dls"],
                    record["type"],
                    record["level_id"],
                    record["format"],
                    record["hash"],
                ),
                tags=tags,
            )
            self.items_data[item_id] = record["meta"]

        total = len(self.all_records)
        shown = len(display_records)
        if total > 0:
            if query or (selected_type and selected_type != "All"):
                self.filter_count_var.set(f"Showing {shown} of {total}")
            else:
                self.filter_count_var.set(f"Total: {total}")
        else:
            self.filter_count_var.set("")

        # Select first matching item if available
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            self._on_tree_select(None)
        else:
            self.selected_var.set("No track selected.")

    def _on_tree_select(self, event):
        selected_ids = self.tree.selection()
        if not selected_ids:
            self.selected_var.set("No track selected.")
            return

        item_id = selected_ids[0]
        meta = self.items_data.get(item_id)
        if meta:
            self.selected_var.set(f"Selected: {meta.get('name', 'Unknown')}")

    def on_search(self):
        self.stop_preview()
        song_id = self.song_id_var.get().strip()
        if not song_id:
            messagebox.showwarning("Input Required", "Please enter a valid Song ID.")
            return

        self._set_ui_busy(True)
        self.status_var.set(f"Searching for Song ID '{song_id}'...")
        self.selected_var.set("No track selected.")
        self.filter_var.set("")

        # Clear existing items and records
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.items_data.clear()
        self.all_records.clear()
        self.filter_count_var.set("")

        # Run network requests in background thread
        threading.Thread(target=self._search_worker, args=(song_id,), daemon=True).start()

    def _search_worker(self, song_id: str):
        original_track_found = False
        nongs = []

        # 1. Ping Newgrounds
        try:
            ng_url = NEWGROUNDS_DOWNLOAD_URL.format(song_id=song_id)
            resp = requests.head(ng_url, headers=DEFAULT_HEADERS, allow_redirects=False, timeout=8)
            # Good response: 200, 302, etc. (< 400)
            if 200 <= resp.status_code < 400:
                original_track_found = True
            elif resp.status_code == 405:  # Method Not Allowed for HEAD
                resp_get = requests.get(ng_url, headers=DEFAULT_HEADERS, allow_redirects=False, stream=True, timeout=8)
                if 200 <= resp_get.status_code < 400:
                    original_track_found = True
        except Exception as e:
            print(f"Error pinging Newgrounds: {e}")

        # 2. Query Song File Hub
        # First attempt: GET https://api.songfilehub.com/song/<song_id>
        try:
            sfh_url = SFH_SONG_URL.format(song_id=song_id)
            resp = requests.get(sfh_url, headers=DEFAULT_HEADERS, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    nongs = data
                elif isinstance(data, dict) and "error" not in data:
                    nongs = [data]
        except Exception as e:
            print(f"Error fetching SFH /song/{song_id}: {e}")

        # Fallback if no NONGs found: query GET https://api.songfilehub.com/songs?songID=<song_id>
        if not nongs:
            try:
                sfh_query_url = SFH_SONGS_QUERY_URL.format(song_id=song_id)
                resp = requests.get(sfh_query_url, headers=DEFAULT_HEADERS, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        nongs = data
                    elif isinstance(data, dict) and "error" not in data:
                        nongs = [data]
            except Exception as e:
                print(f"Error fetching SFH /songs?songID={song_id}: {e}")

        # Send results back to UI
        self.root.after(0, self._display_search_results, song_id, original_track_found, nongs)

    def _display_search_results(self, song_id: str, original_track_found: bool, nongs: list):
        self._set_ui_busy(False)
        self.current_song_id = song_id
        self.all_records = []

        # Insert Original track at the top if found
        if original_track_found:
            self.all_records.append({
                "source": "Original",
                "state": "original",
                "name": "Original track",
                "author": "Newgrounds",
                "dls": "-",
                "raw_dls": None,
                "type": "original",
                "level_id": "-",
                "format": "MP3",
                "hash": "newgrounds",
                "is_original": True,
                "meta": {
                    "is_original": True,
                    "name": "Original track",
                    "song_id": song_id,
                    "download_url": NEWGROUNDS_DOWNLOAD_URL.format(song_id=song_id),
                },
            })

        # Insert NONGs (MP3, WAV, and OGG supported; WAV and OGG are automatically converted to MP3)
        supported_types = ("mp3", "wav", "ogg")
        unsupported_exts = (".zip", ".rar", ".7z", ".flac", ".m4a", ".aac", ".opus", ".wma", ".aiff")
        for nong in nongs:
            # Check explicit filetype attribute
            filetype = (nong.get("filetype") or "").strip().lower()
            if filetype and filetype not in supported_types:
                continue

            # Check URLs or track name for unsupported extensions if filetype was unspecified
            download_url = (nong.get("downloadUrl") or "").lower()
            song_url = (nong.get("songURL") or "").lower()
            name_str = (nong.get("songName") or nong.get("name") or "").lower()
            if any(name_str.endswith(ext) or ext in download_url or ext in song_url for ext in unsupported_exts):
                continue

            name = nong.get("songName") or nong.get("name") or "Unknown Song"
            author = nong.get("createdBy") or "Unknown"
            hash_val = nong.get("urlHash") or nong.get("_id") or ""
            level_id = nong.get("levelID")
            state = nong.get("state") or "unrated"
            downloads = nong.get("downloads")

            # Determine audio format
            if not filetype:
                if name_str.endswith(".wav") or ".wav" in download_url or ".wav" in song_url:
                    filetype = "wav"
                elif name_str.endswith(".ogg") or ".ogg" in download_url or ".ogg" in song_url:
                    filetype = "ogg"
                else:
                    filetype = "mp3"

            format_str = f"{filetype.upper()} -> MP3" if filetype in ("wav", "ogg") else "MP3"
            dls_str = str(downloads) if downloads is not None else "-"
            level_str = str(level_id) if level_id else "-"

            self.all_records.append({
                "source": "NONG",
                "state": state,
                "name": name,
                "author": author,
                "dls": dls_str,
                "raw_dls": downloads,
                "type": state,
                "level_id": level_str,
                "format": format_str,
                "hash": hash_val,
                "is_original": False,
                "meta": {
                    "is_original": False,
                    "name": name,
                    "song_id": song_id,
                    "hash": hash_val,
                    "raw": nong,
                },
            })

        # Dynamically populate the Type/State filter combobox
        states = set()
        for r in self.all_records:
            st = r.get("state")
            if st:
                states.add(st)
        type_options = ["All"] + sorted(list(states), key=lambda s: s.lower())
        self.type_filter_cb["values"] = type_options
        self.type_filter_var.set("All")

        total_items = len(self.all_records)
        if total_items == 0:
            self.status_var.set(f"No tracks found for Song ID '{song_id}'.")
            self.filter_count_var.set("")
            messagebox.showinfo("Not Found", f"No NONGs or Newgrounds track found for Song ID {song_id}.")
        else:
            self.status_var.set(f"Found {total_items} result(s) for Song ID '{song_id}'.")
            self._apply_filter()

    def on_pull(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection Required", "Please select a track from the table to pull.")
            return

        item_id = selected[0]
        meta = self.items_data.get(item_id)
        if not meta:
            messagebox.showerror("Error", "Could not retrieve metadata for selected track.")
            return

        self._set_ui_busy(True)
        track_name = meta.get("name", "Track")
        self.status_var.set(f"Downloading '{track_name}'...")

        threading.Thread(target=self._pull_worker, args=(meta,), daemon=True).start()

    def _write_song_file(self, target_path: str, target_filename: str, audio_bytes: bytes) -> bool:
        """
        Attempts to write audio_bytes to target_path.
        Handles Errno 13 (file in use / open in GD) by showing a special warning
        prompting the user to close the level and press OK to retry, or Cancel to abort.
        If retry fails or Cancel is pressed, shows an error and returns False.
        """
        try:
            with open(target_path, "wb") as f:
                f.write(audio_bytes)
            return True
        except (PermissionError, OSError) as err:
            is_locked = (
                getattr(err, "errno", None) == errno.EACCES
                or getattr(err, "errno", None) == 13
                or "Permission denied" in str(err)
            )
            if not is_locked:
                raise err

            # ErrNo 13: Display special warning prompt
            q = queue.Queue()

            def ask_retry():
                ans = messagebox.askokcancel(
                    "Song Open in Geometry Dash (ErrNo 13)",
                    f"The song file '{target_filename}' is currently open in Geometry Dash.\n\n"
                    "Please close the level in Geometry Dash and press OK to retry, or Cancel to return.",
                    icon="warning",
                )
                q.put(ans)

            self.root.after(0, ask_retry)
            user_agreed_retry = q.get()

            if not user_agreed_retry:
                # Cancel was pressed
                self.root.after(
                    0,
                    messagebox.showerror,
                    "Download Aborted",
                    f"Could not replace '{target_filename}' because it is in use by Geometry Dash.\nReturning to NONG browser.",
                )
                return False

            # OK was pressed: Retry writing once
            try:
                with open(target_path, "wb") as f:
                    f.write(audio_bytes)
                return True
            except (PermissionError, OSError) as retry_err:
                # Failed again on retry
                self.root.after(
                    0,
                    messagebox.showerror,
                    "Retry Failed",
                    f"Failed to overwrite '{target_filename}' on retry.\n\n"
                    "The song file is still locked by Geometry Dash. Please close the level/game and try again.\nReturning to NONG browser.",
                )
                return False

    def _fetch_audio_bytes(self, meta: dict) -> bytes:
        is_original = meta.get("is_original", False)
        song_id = meta.get("song_id") or self.current_song_id

        if is_original:
            download_url = meta.get("download_url") or NEWGROUNDS_DOWNLOAD_URL.format(song_id=song_id)
            resp = requests.get(download_url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=30)
            resp.raise_for_status()
            return resp.content
        else:
            hash_id = meta.get("hash")
            raw = meta.get("raw", {})
            audio_bytes = None

            if hash_id:
                sfh_audio_url = SFH_AUDIO_URL.format(hash_id=hash_id)
                try:
                    resp = requests.get(sfh_audio_url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=20)
                    content_type = resp.headers.get("Content-Type", "")
                    if resp.status_code == 200 and not content_type.startswith("text/html"):
                        if len(resp.content) > 1024:
                            first_bytes = resp.content[:16]
                            if not (b"<!DOCTYPE" in first_bytes.upper() or b"<HTML" in first_bytes.upper()):
                                audio_bytes = resp.content
                except Exception as e:
                    print(f"Direct /audio/ download attempt failed: {e}")

            if audio_bytes is None:
                direct_url = raw.get("downloadUrl") or (SFH_BUCKET_URL.format(hash_id=hash_id) if hash_id else None)
                if direct_url:
                    resp = requests.get(direct_url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=30)
                    resp.raise_for_status()
                    audio_bytes = resp.content
                else:
                    raise ValueError("No download URL or hash available for this NONG.")

            return ensure_mp3_bytes(audio_bytes)

    def _pull_worker(self, meta: dict):
        song_id = meta.get("song_id") or self.current_song_id
        track_name = meta.get("name", "Track")

        try:
            gd_dir = get_gd_directory()
            target_filename = f"{song_id}.mp3"
            target_path = os.path.join(gd_dir, target_filename)

            audio_bytes = self._fetch_audio_bytes(meta)

            write_success = self._write_song_file(target_path, target_filename, audio_bytes)
            if write_success:
                self.root.after(0, self._on_pull_success, track_name, target_path)
            else:
                self.root.after(0, self._reset_after_pull_cancelled)

        except Exception as err:
            self.root.after(0, self._on_pull_error, track_name, str(err))

    def _reset_after_pull_cancelled(self):
        self._set_ui_busy(False)
        self.status_var.set("Ready.")

    def _on_pull_success(self, track_name: str, target_path: str):
        self._set_ui_busy(False)
        self.status_var.set(f"Successfully pulled '{track_name}'!")
        messagebox.showinfo(
            "Success",
            f"Successfully pulled '{track_name}'!\n\nSaved to:\n{target_path}\n\nGeometry Dash will now use this audio file.",
        )

    def _on_pull_error(self, track_name: str, error_msg: str):
        self._set_ui_busy(False)
        self.status_var.set(f"Failed to pull '{track_name}'.")
        messagebox.showerror(
            "Download Error",
            f"Failed to download '{track_name}':\n\n{error_msg}",
        )

    def _get_target_song_id(self) -> str:
        song_id = self.song_id_var.get().strip() or self.current_song_id
        if not song_id:
            prompt_res = simpledialog.askstring(
                "Song ID Required",
                "Enter the target Geometry Dash Song ID to replace:",
                parent=self.root,
            )
            if prompt_res and prompt_res.strip():
                song_id = prompt_res.strip()
                self.song_id_var.set(song_id)
            else:
                return ""
        return song_id

    def on_select_local_file(self):
        song_id = self._get_target_song_id()
        if not song_id:
            return

        file_path = filedialog.askopenfilename(
            parent=self.root,
            title=f"Select Audio File for Song ID {song_id}",
            filetypes=[
                ("Supported Audio (*.mp3;*.wav;*.ogg)", "*.mp3;*.wav;*.ogg"),
                ("MP3 Audio (*.mp3)", "*.mp3"),
                ("WAV Audio (*.wav)", "*.wav"),
                ("OGG Audio (*.ogg)", "*.ogg"),
                ("All Files (*.*)", "*.*"),
            ],
        )
        if not file_path:
            return

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".mp3", ".wav", ".ogg"):
            messagebox.showerror(
                "Invalid File Type",
                "Unsupported audio file type.\nPlease select an MP3, WAV, or OGG file (WAV and OGG will be automatically converted to MP3 for Geometry Dash).",
            )
            return

        try:
            with open(file_path, "rb") as f:
                raw_bytes = f.read()

            if not raw_bytes:
                messagebox.showerror("Error", "The selected file is empty.")
                return

            self.status_var.set(f"Processing '{os.path.basename(file_path)}'...")
            self.root.update_idletasks()

            audio_bytes = ensure_mp3_bytes(raw_bytes)

            gd_dir = get_gd_directory()
            target_filename = f"{song_id}.mp3"
            target_path = os.path.join(gd_dir, target_filename)

            # Write file with ErrNo 13 handling
            write_success = self._write_song_file(target_path, target_filename, audio_bytes)
            if write_success:
                source_name = os.path.basename(file_path)
                converted_note = " (converted to MP3)" if ext in (".wav", ".ogg") else ""
                self.status_var.set(f"Successfully installed '{source_name}'{converted_note} as '{target_filename}'!")
                messagebox.showinfo(
                    "Success",
                    f"Successfully installed '{source_name}'{converted_note} as '{target_filename}'!\n\n"
                    f"Saved to:\n{target_path}\n\nGeometry Dash will now use this audio file.",
                )
        except Exception as err:
            messagebox.showerror("File Error", f"Failed to process or copy audio file:\n\n{err}")

    def on_pull_from_url(self):
        song_id = self._get_target_song_id()
        if not song_id:
            return

        url = simpledialog.askstring(
            "Pull from URL",
            f"Enter direct audio URL for Song ID {song_id} (MP3, WAV, or OGG):",
            parent=self.root,
        )
        if not url or not url.strip():
            return
        url = url.strip()

        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showerror("Invalid URL", "Please enter a valid HTTP or HTTPS URL.")
            return

        unsupported_exts = (".zip", ".rar", ".7z", ".flac", ".m4a", ".aac", ".opus", ".wma", ".aiff")
        url_path = url.split("?")[0].lower()
        if any(url_path.endswith(ext) for ext in unsupported_exts):
            messagebox.showerror(
                "Invalid Audio Format",
                "NONGman currently supports .mp3, .wav, and .ogg files (WAV and OGG will be automatically converted to MP3).",
            )
            return

        self._set_ui_busy(True)
        self.status_var.set(f"Downloading audio from URL for Song ID '{song_id}'...")

        threading.Thread(target=self._pull_url_worker, args=(url, song_id), daemon=True).start()

    def _pull_url_worker(self, url: str, song_id: str):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=30)
            resp.raise_for_status()
            raw_bytes = resp.content

            if len(raw_bytes) < 100 or b"<!DOCTYPE" in raw_bytes[:50].upper() or b"<HTML" in raw_bytes[:50].upper():
                raise ValueError("The provided URL returned an HTML web page rather than direct audio.\nPlease provide a direct download URL.")

            self.root.after(0, lambda: self.status_var.set(f"Converting audio for Song ID '{song_id}'..."))
            audio_bytes = ensure_mp3_bytes(raw_bytes)

            gd_dir = get_gd_directory()
            target_filename = f"{song_id}.mp3"
            target_path = os.path.join(gd_dir, target_filename)

            write_success = self._write_song_file(target_path, target_filename, audio_bytes)
            if write_success:
                self.root.after(0, self._on_pull_success, f"URL Audio ({song_id})", target_path)
            else:
                self.root.after(0, self._reset_after_pull_cancelled)

        except Exception as err:
            self.root.after(0, self._on_pull_error, f"URL Audio ({song_id})", str(err))

    def _on_tree_right_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if item_id:
            self.tree.selection_set(item_id)
            self._on_tree_select(None)
            self.context_menu.post(event.x_root, event.y_root)

    def _get_preview_file_path(self, meta: dict) -> str:
        temp_dir = os.path.join(tempfile.gettempdir(), "NONGman_previews")
        os.makedirs(temp_dir, exist_ok=True)
        if meta.get("is_original"):
            song_id = meta.get("song_id") or "original"
            filename = f"preview_ng_{song_id}.mp3"
        else:
            hash_val = meta.get("hash") or meta.get("raw", {}).get("_id") or "nong"
            safe_hash = "".join(c for c in hash_val if c.isalnum() or c in "-_")
            filename = f"preview_{safe_hash}.mp3"
        return os.path.join(temp_dir, filename)

    def on_preview(self):
        if self.is_playing_preview:
            self.stop_preview()
            return

        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection Required", "Please select a track from the table to preview.")
            return

        item_id = selected[0]
        meta = self.items_data.get(item_id)
        if not meta:
            return

        track_name = meta.get("name", "Track")
        preview_file = self._get_preview_file_path(meta)

        if os.path.exists(preview_file) and os.path.getsize(preview_file) > 1024:
            self._start_mci_playback(preview_file, track_name)
        else:
            self.preview_btn.config(text="Loading...")
            self.status_var.set(f"Fetching preview for '{track_name}'...")
            threading.Thread(target=self._preview_worker, args=(meta, preview_file, False), daemon=True).start()

    def on_preview_external(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection Required", "Please select a track from the table to preview.")
            return

        item_id = selected[0]
        meta = self.items_data.get(item_id)
        if not meta:
            return

        track_name = meta.get("name", "Track")
        preview_file = self._get_preview_file_path(meta)

        if os.path.exists(preview_file) and os.path.getsize(preview_file) > 1024:
            os.startfile(preview_file)
            self.status_var.set(f"Opened '{track_name}' in external player.")
        else:
            self.status_var.set(f"Fetching preview for '{track_name}'...")
            threading.Thread(target=self._preview_worker, args=(meta, preview_file, True), daemon=True).start()

    def _preview_worker(self, meta: dict, preview_file: str, open_external: bool):
        track_name = meta.get("name", "Track")
        try:
            audio_bytes = self._fetch_audio_bytes(meta)
            with open(preview_file, "wb") as f:
                f.write(audio_bytes)

            if open_external:
                os.startfile(preview_file)
                self.root.after(0, lambda: self.status_var.set(f"Opened '{track_name}' in external player."))
                self.root.after(0, lambda: self.preview_btn.config(text="Preview"))
            else:
                self.root.after(0, self._start_mci_playback, preview_file, track_name)
        except Exception as err:
            self.root.after(0, lambda: self.preview_btn.config(text="Preview"))
            self.root.after(0, lambda: self.status_var.set("Failed to load preview."))
            self.root.after(0, messagebox.showerror, "Preview Error", f"Could not load preview for '{track_name}':\n\n{err}")

    def _start_mci_playback(self, preview_file: str, track_name: str):
        self.stop_preview()
        try:
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW("close nong_preview", None, 0, 0)

            cmd_open = f'open "{preview_file}" type mpegvideo alias nong_preview'
            res_open = winmm.mciSendStringW(cmd_open, None, 0, 0)
            if res_open != 0:
                os.startfile(preview_file)
                self.status_var.set(f"Opened '{track_name}' in external player.")
                return

            res_play = winmm.mciSendStringW("play nong_preview", None, 0, 0)
            if res_play != 0:
                os.startfile(preview_file)
                self.status_var.set(f"Opened '{track_name}' in external player.")
                return

            self.is_playing_preview = True
            self.preview_btn.config(text="Stop Listening")
            self.status_var.set(f"Playing preview: '{track_name}'")
            self._poll_preview_status()
        except Exception as e:
            print(f"Error starting playback: {e}")
            os.startfile(preview_file)

    def stop_preview(self):
        if self.is_playing_preview:
            try:
                winmm = ctypes.windll.winmm
                winmm.mciSendStringW("stop nong_preview", None, 0, 0)
                winmm.mciSendStringW("close nong_preview", None, 0, 0)
            except Exception:
                pass
            self.is_playing_preview = False
            self.preview_btn.config(text="Preview")

    def _poll_preview_status(self):
        if not self.is_playing_preview:
            return

        buf = ctypes.create_unicode_buffer(64)
        winmm = ctypes.windll.winmm
        res = winmm.mciSendStringW("status nong_preview mode", buf, 64, 0)
        if res == 0 and buf.value.lower() == "stopped":
            self.stop_preview()
            self.status_var.set("Ready.")
        else:
            self.root.after(500, self._poll_preview_status)

    def on_closing(self):
        self.stop_preview()
        self.root.destroy()

    def _check_for_updates(self):
        try:
            resp = requests.get(GH_API_LATEST_RELEASE, headers=DEFAULT_HEADERS, timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "").strip()
                if tag:
                    # Compare version string with tag on GH
                    clean_latest = tag.lstrip("vV")
                    clean_current = VERSION.lstrip("vV")
                    if clean_latest != clean_current and tag != VERSION:
                        self.root.after(0, self._show_update_available, tag)
        except Exception as e:
            print(f"Error checking for updates: {e}")

    def _show_update_available(self, latest_tag: str):
        self.update_label.config(text=f"(Update! {VERSION} -> {latest_tag})")
        self.update_label.pack(side=tk.LEFT, padx=(6, 0))


def main():
    root = tk.Tk()
    app = NongManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
