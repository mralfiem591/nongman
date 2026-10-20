import errno
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
import requests

SFH_SONG_URL = "https://api.songfilehub.com/song/{song_id}"
SFH_SONGS_QUERY_URL = "https://api.songfilehub.com/songs?songID={song_id}"
SFH_AUDIO_URL = "https://api.songfilehub.com/audio/{hash_id}"
SFH_BUCKET_URL = "https://bucket.gdmobilewrlist.com/api/bucket/file/{hash_id}"
NEWGROUNDS_DOWNLOAD_URL = "https://www.newgrounds.com/audio/download/{song_id}"

GH_REPO_URL = "https://github.com/mralfiem591/nongman"
GH_API_LATEST_RELEASE = "https://api.github.com/repos/mralfiem591/nongman/releases/latest"

VERSION = "1.0.2"

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


class NongManagerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Geometry Dash NONG Manager")
        self.root.geometry("820x520")
        self.root.minsize(680, 400)

        self.current_song_id = ""
        self.all_records = []  # List of dicts representing all fetched results
        self.items_data = {}   # tree item id -> metadata dict for currently visible items

        self._build_ui()
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

        # Text Filter (filters Name and Author only)
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

        # Sort By Combobox (Name, Author, Hash)
        ttk.Label(controls_frame, text="Sort By:").pack(side=tk.LEFT, padx=(0, 4))
        self.sort_by_var = tk.StringVar(value="Name")
        self.sort_by_cb = ttk.Combobox(
            controls_frame,
            textvariable=self.sort_by_var,
            state="readonly",
            width=8,
            values=["Name", "Author", "Hash"],
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

        columns = ("type", "name", "author", "details", "hash")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("type", text="Type")
        self.tree.heading("name", text="Track Name")
        self.tree.heading("author", text="Author / Creator")
        self.tree.heading("details", text="Details")
        self.tree.heading("hash", text="Audio Hash")

        self.tree.column("type", width=110, minwidth=90, stretch=False)
        self.tree.column("name", width=280, minwidth=180)
        self.tree.column("author", width=140, minwidth=100)
        self.tree.column("details", width=120, minwidth=80)
        self.tree.column("hash", width=110, minwidth=80)

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
            text=f"NONGman by mralfiem591. GitHub: {GH_REPO_URL}",
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
        self.pull_btn.pack(side=tk.RIGHT)

        self.selected_var = tk.StringVar(value="No track selected.")
        self.selected_label = ttk.Label(bottom_frame, textvariable=self.selected_var)
        self.selected_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda event: self.on_pull())

    def _set_ui_busy(self, busy: bool):
        state = tk.DISABLED if busy else tk.NORMAL
        self.search_btn.config(state=state)
        self.pull_btn.config(state=state)
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

            # Text filter: ONLY search by Name and Author (do NOT search Details or Type)
            if query:
                name_match = query in record["name"].lower()
                author_match = query in record["author"].lower()
                if not (name_match or author_match):
                    continue

            filtered_records.append(record)

        # 2. Sort records
        sort_field = self.sort_by_var.get().strip().lower()  # "name", "author", or "hash"
        reverse = (self.sort_dir_var.get() == "D")

        # Keep original track at the top if present, sort NONGs
        originals = [r for r in filtered_records if r["is_original"]]
        nongs = [r for r in filtered_records if not r["is_original"]]

        def get_sort_key(r):
            val = r.get(sort_field) or ""
            return val.lower()

        nongs.sort(key=get_sort_key, reverse=reverse)
        display_records = originals + nongs

        # 3. Populate Treeview
        for record in display_records:
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    record["type_label"],
                    record["name"],
                    record["author"],
                    record["details"],
                    record["hash"],
                ),
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
                "type_label": "Original",
                "state": "original",
                "name": "Original track",
                "author": "Newgrounds",
                "details": "Official Newgrounds Audio",
                "hash": "newgrounds",
                "is_original": True,
                "meta": {
                    "is_original": True,
                    "name": "Original track",
                    "song_id": song_id,
                    "download_url": NEWGROUNDS_DOWNLOAD_URL.format(song_id=song_id),
                },
            })

        # Insert NONGs
        for nong in nongs:
            name = nong.get("songName") or nong.get("name") or "Unknown Song"
            author = nong.get("createdBy") or "Unknown"
            hash_val = nong.get("urlHash") or nong.get("_id") or ""
            level_id = nong.get("levelID")
            state = nong.get("state") or "unrated"
            downloads = nong.get("downloads")

            details_parts = []
            if level_id:
                details_parts.append(f"Level: {level_id}")
            if state:
                details_parts.append(str(state))
            if downloads is not None:
                details_parts.append(f"{downloads} DLs")
            details = " | ".join(details_parts) if details_parts else "NONG"

            self.all_records.append({
                "type_label": "NONG",
                "state": state,
                "name": name,
                "author": author,
                "details": details,
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

    def _pull_worker(self, meta: dict):
        song_id = meta.get("song_id") or self.current_song_id
        is_original = meta.get("is_original", False)
        track_name = meta.get("name", "Track")

        try:
            gd_dir = get_gd_directory()
            target_filename = f"{song_id}.mp3"
            target_path = os.path.join(gd_dir, target_filename)
            audio_bytes = None

            if is_original:
                # Download from Newgrounds
                download_url = meta.get("download_url") or NEWGROUNDS_DOWNLOAD_URL.format(song_id=song_id)
                resp = requests.get(download_url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=30)
                resp.raise_for_status()
                audio_bytes = resp.content
            else:
                # Download NONG
                hash_id = meta.get("hash")
                raw = meta.get("raw", {})

                # 1. Attempt GET https://api.songfilehub.com/audio/<unique_audio_hash>
                if hash_id:
                    sfh_audio_url = SFH_AUDIO_URL.format(hash_id=hash_id)
                    try:
                        resp = requests.get(sfh_audio_url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=20)
                        content_type = resp.headers.get("Content-Type", "")

                        # Ensure it's not returning an HTML wrapper page
                        if resp.status_code == 200 and not content_type.startswith("text/html"):
                            if len(resp.content) > 1024:
                                first_bytes = resp.content[:16]
                                if not (b"<!DOCTYPE" in first_bytes.upper() or b"<HTML" in first_bytes.upper()):
                                    audio_bytes = resp.content
                    except Exception as e:
                        print(f"Direct /audio/ download attempt failed: {e}")

                # 2. Fallback to downloadUrl or direct bucket if the direct endpoint returned HTML or failed
                if audio_bytes is None:
                    direct_url = raw.get("downloadUrl") or (SFH_BUCKET_URL.format(hash_id=hash_id) if hash_id else None)
                    if direct_url:
                        resp = requests.get(direct_url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=30)
                        resp.raise_for_status()
                        audio_bytes = resp.content
                    else:
                        raise ValueError("No download URL or hash available for this NONG.")

            # Attempt writing file with ErrNo 13 handling
            write_success = self._write_song_file(target_path, target_filename, audio_bytes)

            if write_success:
                self.root.after(0, self._on_pull_success, track_name, target_path)
            else:
                # Return safely to NONG browser
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
