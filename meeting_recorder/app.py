"""Tkinter desktop window: record, watch for meetings, browse and copy transcripts."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import detect, meetings, startup, tray
from .captions import Caption, LiveCaptioner
from .audio import Recorder, list_devices
from .config import Settings, settings_dir
from .transcribe import fmt_clock, load_model

CONSENT_TEXT = (
    "Let everyone in the meeting know that you're recording, ideally before you start "
    "or as soon as the meeting begins.\n\n"
    "Recording laws differ by country, state and province. Many places require every "
    "participant's consent. Follow the laws that apply to you and to the people you're "
    "meeting with, and your organization's guidelines and policies."
)

IS_MAC = sys.platform == "darwin"

POLL_MS = 200
DETECT_MS = 3000
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"]


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class Transcriber(threading.Thread):
    """One background worker that transcribes meetings in order and keeps the model loaded."""

    def __init__(self, app: "App"):
        super().__init__(daemon=True, name="transcriber")
        self.app = app
        self.jobs: queue.Queue[meetings.Meeting] = queue.Queue()
        self._model = None
        self._model_key: tuple[str, str] | None = None
        self.busy = False

    def submit(self, meeting: meetings.Meeting) -> None:
        self.jobs.put(meeting)

    def run(self) -> None:
        while True:
            meeting = self.jobs.get()
            self.busy = True
            try:
                self._handle(meeting)
            finally:
                self.busy = False

    def _handle(self, meeting: meetings.Meeting) -> None:
        s = self.app.settings
        key = (s.whisper_model, s.whisper_device)
        if self._model_key != key:
            self.app.post(lambda: self.app.set_status(
                f"Loading Whisper '{s.whisper_model}' (first run downloads it)..."))
            try:
                self._model = load_model(*key)
                self._model_key = key
            except Exception as exc:
                meeting.status, meeting.error = meetings.STATUS_ERROR, f"Model load failed: {exc}"
                meeting.save()
                self.app.post(lambda: (self.app.set_status(meeting.error), self.app.refresh_list()))
                return

        def progress(frac: float, who: str, m=meeting) -> None:
            self.app.post(lambda: self.app.set_status(
                f"Transcribing '{m.title}': {frac:.0%} ({who})"))

        self.app.post(self.app.refresh_list)
        done = meetings.process(meeting, s, self._model, progress)
        msg = (f"Transcript ready: {done.title}" if done.status == meetings.STATUS_DONE
               else f"Transcription failed: {done.error}")
        self.app.post(lambda: (self.app.set_status(msg), self.app.refresh_list(),
                               self.app.show_meeting(done.folder), self.app.notify_if_hidden(msg)))


class App:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.root = tk.Tk()
        self.root.title("Meeting Recorder")
        _set_icon(self.root)
        self.root.geometry("1000x640")
        self.root.minsize(760, 480)
        self._ui_calls: queue.Queue = queue.Queue()

        self.recorder: Recorder | None = None
        self.current: meetings.Meeting | None = None
        self.auto_started_by: str | None = None
        self.missing_since: float | None = None
        self.snoozed_app: str | None = None
        self.prompt_win: tk.Toplevel | None = None
        self.consent_win: tk.Toplevel | None = None
        self.caption_win: CaptionWindow | None = None
        self.captioner: LiveCaptioner | None = None
        self._live_model = None
        self._live_model_name: str | None = None
        self.selected_folder: Path | None = None

        self.worker = Transcriber(self)
        self.worker.start()

        self._build()
        self._recover_interrupted()
        self.refresh_list()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tray = tray.create(self)
        if IS_MAC:
            self._setup_mac()
        self.root.after(POLL_MS, self._tick)
        self.root.after(DETECT_MS, self._detect_tick)

    # ---- thread-safe UI calls ------------------------------------------------------

    def post(self, fn) -> None:
        self._ui_calls.put(fn)

    def _tick(self) -> None:
        while not self._ui_calls.empty():
            self._ui_calls.get()()
        if self.recorder:
            mic, sysl = self.recorder.levels
            self.mic_bar["value"] = min(mic * 400, 100)
            self.sys_bar["value"] = min(sysl * 400, 100)
            self.elapsed_var.set(fmt_clock(self.recorder.elapsed))
            errors = self.recorder.errors
            if errors:
                self.stop_recording()
                self.show_window()
                messagebox.showerror("Recording stopped", "\n".join(errors))
        self.root.after(POLL_MS, self._tick)

    # ---- layout ---------------------------------------------------------------------

    def _build(self) -> None:
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=False)
        filemenu.add_command(label="Settings...", command=self.open_settings)
        filemenu.add_command(label="Open meetings folder", command=self.open_output_dir)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.quit)
        menubar.add_cascade(label="File", menu=filemenu)
        self.root.config(menu=menubar)

        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Meeting title").grid(row=0, column=0, sticky="w")
        self.title_var = tk.StringVar()
        self.title_entry = ttk.Entry(top, textvariable=self.title_var, width=40)
        self.title_entry.grid(row=0, column=1, padx=6, sticky="we")
        self.record_btn = ttk.Button(top, text="● Record", command=self.toggle_recording, width=14)
        self.record_btn.grid(row=0, column=2, padx=6)
        self.elapsed_var = tk.StringVar(value="00:00:00")
        ttk.Label(top, textvariable=self.elapsed_var, font=("Segoe UI", 14)).grid(row=0, column=3, padx=6)
        ttk.Button(top, text="CC  Live captions", command=self.toggle_captions).grid(row=0, column=4, padx=6)

        meters = ttk.Frame(top)
        meters.grid(row=1, column=0, columnspan=4, sticky="we", pady=(8, 0))
        ttk.Label(meters, text="You (mic)").pack(side="left")
        self.mic_bar = ttk.Progressbar(meters, length=180, maximum=100)
        self.mic_bar.pack(side="left", padx=(4, 16))
        ttk.Label(meters, text="Others (system audio)").pack(side="left")
        self.sys_bar = ttk.Progressbar(meters, length=180, maximum=100)
        self.sys_bar.pack(side="left", padx=4)
        top.columnconfigure(1, weight=1)

        panes = ttk.PanedWindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10)

        left = ttk.Frame(panes)
        cols = ("date", "title", "length", "status")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for col, width in zip(cols, (120, 200, 70, 90)):
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, width=width, stretch=(col == "title"))
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        panes.add(left, weight=1)

        right = ttk.Frame(panes)
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(buttons, text="Copy transcript", command=self.copy_transcript).pack(side="left")
        ttk.Button(buttons, text="Open folder", command=self.open_selected_folder).pack(side="left", padx=6)
        ttk.Button(buttons, text="Play audio", command=self.play_selected).pack(side="left")
        ttk.Button(buttons, text="Re-transcribe", command=self.retranscribe).pack(side="left", padx=6)
        text_frame = ttk.Frame(right)
        text_frame.pack(fill="both", expand=True)
        self.text = tk.Text(text_frame, wrap="word", font=("Segoe UI", 10), state="disabled",
                            padx=8, pady=8)
        scroll = ttk.Scrollbar(text_frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        panes.add(right, weight=2)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(10, 4)).pack(fill="x")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    # ---- recording ------------------------------------------------------------------

    def toggle_recording(self) -> None:
        if self.recorder:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self, auto_app: str | None = None) -> None:
        if self.recorder:
            return
        if IS_MAC and not self._mac_permission_ok():
            return
        title = self.title_var.get() or (f"{auto_app} meeting" if auto_app else "")
        try:
            meeting = meetings.new_meeting(self.settings, title)
        except OSError as exc:
            messagebox.showerror("Can't create meeting folder", str(exc))
            return
        rec = Recorder(meeting.folder, self.settings.sample_rate,
                       self.settings.mic_device, self.settings.speaker_device)
        try:
            rec.start()
        except Exception as exc:
            meeting.status, meeting.error = meetings.STATUS_ERROR, str(exc)
            meeting.save()
            messagebox.showerror("Can't start recording", str(exc))
            return
        self.recorder, self.current = rec, meeting
        self.auto_started_by = auto_app
        self.missing_since = None
        self.record_btn.config(text="■ Stop")
        self.title_entry.config(state="disabled")
        self.set_status(f"Recording '{meeting.title}'" + (f" (started for {auto_app})" if auto_app else ""))
        if self.tray:
            self.tray.set_recording(True, meeting.title)
        self.refresh_list()
        if self.settings.live_captions or self.caption_win:
            self._open_caption_window()
            self._start_captioner()
        if self.settings.consent_reminder:
            self._show_consent_reminder()

    def stop_recording(self, transcribe: bool | None = None) -> None:
        if not self.recorder or not self.current:
            return
        rec, meeting = self.recorder, self.current
        self.recorder = self.current = None
        self._stop_captioner()
        rec.stop()
        meeting.duration = rec.elapsed
        meeting.status = meetings.STATUS_RECORDED
        meeting.save()
        self.auto_started_by = None
        self.record_btn.config(text="● Record")
        self.title_entry.config(state="normal")
        self.title_var.set("")
        self.mic_bar["value"] = self.sys_bar["value"] = 0
        if self.tray:
            self.tray.set_recording(False)
        self.set_status(f"Saved '{meeting.title}' ({fmt_clock(meeting.duration)}).")
        self.notify_if_hidden(f"Saved '{meeting.title}' ({fmt_clock(meeting.duration)}).")
        if self.settings.auto_process if transcribe is None else transcribe:
            self.worker.submit(meeting)
            self.set_status(f"Saved '{meeting.title}'. Queued for transcription.")
        self.refresh_list()

    # ---- meeting detection ----------------------------------------------------------

    def _detect_tick(self) -> None:
        try:
            if self.settings.auto_detect:
                self._check_meeting(detect.active_meeting_app())
        finally:
            self.root.after(DETECT_MS, self._detect_tick)

    def _check_meeting(self, app: str | None) -> None:
        if self.recorder:
            if self.auto_started_by and self.settings.auto_stop:
                if app:
                    self.missing_since = None
                elif self.missing_since is None:
                    self.missing_since = time.monotonic()
                elif time.monotonic() - self.missing_since >= self.settings.auto_stop_grace_seconds:
                    self.stop_recording()
            return
        if app is None:
            self.snoozed_app = None
            self._close_prompt()
            return
        if app != self.snoozed_app and self.prompt_win is None:
            self._show_prompt(app)

    def _show_prompt(self, app: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("Meeting detected")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=16)
        frame.pack()
        ttk.Label(frame, text=f"{app} is using your microphone.\nRecord this meeting?",
                  justify="left").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(12, 0))

        def record() -> None:
            self._close_prompt()
            self.start_recording(auto_app=app)

        def snooze() -> None:
            self.snoozed_app = app
            self._close_prompt()

        ttk.Button(row, text="Record", command=record).pack(side="left")
        ttk.Button(row, text="Not now", command=snooze).pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", snooze)
        self.prompt_win = win

    def _show_consent_reminder(self) -> None:
        """Shown as recording starts. It doesn't block the recording, so no audio is lost."""
        win = tk.Toplevel(self.root)
        win.title("You're recording")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=16)
        frame.pack()
        ttk.Label(frame, text="Tell participants you're recording",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text=CONSENT_TEXT, wraplength=380, justify="left").pack(anchor="w", pady=(8, 0))
        dont_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Don't show this again", variable=dont_show).pack(anchor="w", pady=(12, 0))

        def ok() -> None:
            if dont_show.get():
                self.settings.consent_reminder = False
                self.settings.save()
            win.destroy()

        ttk.Button(frame, text="OK", command=ok).pack(anchor="e", pady=(12, 0))
        win.protocol("WM_DELETE_WINDOW", ok)
        win.bind("<Return>", lambda _e: ok())
        self.consent_win = win

    # ---- live captions ----------------------------------------------------------------

    def toggle_captions(self) -> None:
        if self.caption_win:
            self._close_caption_window()
        else:
            self._open_caption_window()
            if self.recorder:
                self._start_captioner()

    def _open_caption_window(self) -> None:
        if self.caption_win is None:
            self.caption_win = CaptionWindow(self)
        self.caption_win.set_status("" if self.recorder else "Captions appear here while you record.")

    def _close_caption_window(self) -> None:
        self._stop_captioner()
        if self.caption_win:
            self.caption_win.destroy()
            self.caption_win = None

    def _live_model_name_for_settings(self) -> str:
        name = self.settings.live_caption_model
        # English-only variants are faster and more accurate when the language is fixed.
        if self.settings.language == "en" and name in ("tiny", "base", "small", "medium"):
            return f"{name}.en"
        return name

    def _load_live_model(self):
        # Runs on the captioner thread. Keep the model between meetings.
        name = self._live_model_name_for_settings()
        if self._live_model is None or self._live_model_name != name:
            self._live_model = load_model(name, self.settings.whisper_device)
            self._live_model_name = name
        return self._live_model

    def _start_captioner(self) -> None:
        if self.captioner or not self.recorder:
            return
        win = self.caption_win
        self.captioner = LiveCaptioner(
            self._load_live_model,
            (self.settings.your_name, self.settings.others_label),
            on_caption=lambda c: self.post(lambda: win and win.winfo_exists() and win.show(c)),
            language=self.settings.language,
            on_status=lambda msg: self.post(lambda: win and win.winfo_exists() and win.set_status(msg)),
        )
        self.recorder.listeners.append(self.captioner.feed)
        self.captioner.start()

    def _stop_captioner(self) -> None:
        cap, self.captioner = self.captioner, None
        if cap is None:
            return
        if self.recorder and cap.feed in self.recorder.listeners:
            self.recorder.listeners.remove(cap.feed)
        cap.stop()
        if self.caption_win:
            self.caption_win.set_status("Recording stopped.")

    def _close_prompt(self) -> None:
        if self.prompt_win is not None:
            self.prompt_win.destroy()
            self.prompt_win = None

    # ---- meeting list and viewer ----------------------------------------------------

    def _recover_interrupted(self) -> None:
        """Meetings left mid-recording or mid-transcription by a crash or quit."""
        for m in meetings.list_meetings(self.settings):
            if m.status in (meetings.STATUS_RECORDING, meetings.STATUS_TRANSCRIBING):
                m.status = meetings.STATUS_RECORDED
                m.save()

    def refresh_list(self) -> None:
        keep = self.selected_folder
        self.tree.delete(*self.tree.get_children())
        for m in meetings.list_meetings(self.settings):
            length = fmt_clock(m.duration) if m.duration else ""
            self.tree.insert("", "end", iid=str(m.folder),
                             values=(m.date_str, m.title, length, m.status))
        if keep and self.tree.exists(str(keep)):
            self.tree.selection_set(str(keep))

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if sel:
            self.selected_folder = Path(sel[0])
            self._render(self.selected_folder)

    def show_meeting(self, folder: Path) -> None:
        if self.tree.exists(str(folder)):
            self.tree.selection_set(str(folder))
            self.tree.see(str(folder))

    def _render(self, folder: Path) -> None:
        try:
            meeting = meetings.Meeting.load(folder)
        except Exception:
            return
        if meeting.transcript_txt.exists():
            body = meeting.transcript_txt.read_text(encoding="utf-8")
        elif meeting.status == meetings.STATUS_ERROR:
            body = f"Something went wrong:\n\n{meeting.error}\n\nTry Re-transcribe."
        elif meeting.status == meetings.STATUS_RECORDED:
            body = "Not transcribed yet. Click Re-transcribe."
        else:
            body = f"Status: {meeting.status}"
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", f"{meeting.title}\n{meeting.date_str}\n\n{body}")
        self.text.config(state="disabled")

    def _selected(self) -> meetings.Meeting | None:
        if not self.selected_folder:
            messagebox.showinfo("No meeting selected", "Pick a meeting from the list first.")
            return None
        return meetings.Meeting.load(self.selected_folder)

    def copy_transcript(self) -> None:
        m = self._selected()
        if not m:
            return
        if not m.transcript_txt.exists():
            messagebox.showinfo("No transcript yet", "This meeting hasn't been transcribed.")
            return
        text = f"Meeting: {m.title}\nDate: {m.date_str}\n\n" + m.transcript_txt.read_text(encoding="utf-8")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("Transcript copied. Paste it into the AI tool of your choice.")

    def open_selected_folder(self) -> None:
        m = self._selected()
        if m:
            open_path(m.folder)

    def play_selected(self) -> None:
        m = self._selected()
        if m and (m.folder / "meeting.wav").exists():
            open_path(m.folder / "meeting.wav")
        elif m:
            messagebox.showinfo("No audio", "No mixed audio file yet. Transcribe the meeting first.")

    def retranscribe(self) -> None:
        m = self._selected()
        if not m:
            return
        if self.current and m.folder == self.current.folder:
            messagebox.showinfo("Still recording", "Stop the recording first.")
            return
        self.worker.submit(m)
        self.set_status(f"Queued '{m.title}' for transcription.")

    def open_output_dir(self) -> None:
        path = Path(self.settings.output_dir)
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)

    # ---- settings -------------------------------------------------------------------

    def open_settings(self) -> None:
        SettingsDialog(self)

    # ---- shutdown -------------------------------------------------------------------

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def is_hidden(self) -> bool:
        return self.root.state() in ("withdrawn", "iconic")

    @property
    def can_hide(self) -> bool:
        """Windows hides to the tray. macOS keeps the app in the Dock, the usual Mac way."""
        return self.tray is not None or IS_MAC

    def notify(self, message: str) -> None:
        if self.tray:
            self.tray.notify(message)
        elif IS_MAC:
            script = f'display notification {json.dumps(message)} with title "Meeting Recorder"'
            subprocess.run(["osascript", "-e", script], check=False)

    def notify_if_hidden(self, message: str) -> None:
        if self.can_hide and self.is_hidden():
            self.notify(message)

    def hide_to_tray(self) -> None:
        self.root.withdraw()
        if not self.settings.tray_notice_shown:
            self.notify("Still running in the Dock, watching for meetings. Press Cmd+Q to quit."
                        if IS_MAC else
                        "Still running in the tray, watching for meetings. "
                        "Right-click the icon to quit.")
            self.settings.tray_notice_shown = True
            self.settings.save()

    def on_close(self) -> None:
        """The window's X button."""
        if self.can_hide and self.settings.close_to_tray:
            self.hide_to_tray()
        else:
            self.quit()

    def _setup_mac(self) -> None:
        # Standard Mac app behavior: Dock click reopens, Cmd+Q quits, Cmd+, opens Settings.
        self.root.createcommand("::tk::mac::ReopenApplication", self.show_window)
        self.root.createcommand("::tk::mac::Quit", self.quit)
        self.root.createcommand("::tk::mac::ShowPreferences", self.open_settings)
        # First launch of the installed app: turn on start-at-login, the same default
        # the Windows installer sets. Settings can turn it off.
        if getattr(sys, "frozen", False) and not self.settings.login_item_default_applied:
            try:
                startup.set_enabled(True)
            except OSError:
                pass
            self.settings.login_item_default_applied = True
            self.settings.save()

    def _mac_permission_ok(self) -> bool:
        """Asks for Screen & System Audio Recording permission if it's missing."""
        from . import macos

        if macos.has_screen_permission():
            return True
        if macos.request_screen_permission():
            return True
        self.show_window()
        if messagebox.askyesno(
                "Permission needed",
                "To record the other people on the call, Meeting Recorder needs Screen & "
                "System Audio Recording permission. It only uses the audio.\n\n"
                "Open System Settings to turn it on? After you do, quit and reopen "
                "Meeting Recorder."):
            macos.open_permission_settings()
        return False

    def quit(self) -> None:
        if self.recorder or self.worker.busy or not self.worker.jobs.empty():
            self.show_window()  # so the question isn't asked behind a hidden window
        if self.recorder:
            if not messagebox.askyesno("Recording in progress",
                                       "Stop the recording and quit? The audio is kept."):
                return
            self.stop_recording(transcribe=False)
        elif self.worker.busy or not self.worker.jobs.empty():
            if not messagebox.askyesno("Transcription in progress",
                                       "Quit anyway? You can re-transcribe the meeting later."):
                return
        self._stop_captioner()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


class CaptionWindow(tk.Toplevel):
    """A dark, large-text window that shows the last few lines of speech, like TV captions."""

    MAX_LINES = 8
    COLORS = {"bg": "#111418", "fg": "#f2f2f2", "draft": "#9aa0a6",
              0: "#8ecbff", 1: "#ffd479"}  # your lines blue, other side amber

    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.title("Live captions")
        self.geometry("760x240")
        self.configure(bg=self.COLORS["bg"])
        self.captions: dict[int, Caption] = {}
        self.on_top = tk.BooleanVar(value=True)
        self.attributes("-topmost", True)

        # Pack the bottom bar first so the text area can't squeeze it out.
        bar = tk.Frame(self, bg=self.COLORS["bg"])
        bar.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        tk.Checkbutton(bar, text="Keep on top", variable=self.on_top, command=self._apply_top,
                       bg=self.COLORS["bg"], fg=self.COLORS["draft"], selectcolor=self.COLORS["bg"],
                       activebackground=self.COLORS["bg"], activeforeground=self.COLORS["fg"],
                       highlightthickness=0).pack(side="left")
        self.status = tk.Label(bar, text="", bg=self.COLORS["bg"], fg=self.COLORS["draft"],
                               font=("Segoe UI", 10))
        self.status.pack(side="right")
        tk.Label(bar, text="Live captions are a rough preview. The saved transcript is more accurate.",
                 bg=self.COLORS["bg"], fg=self.COLORS["draft"], font=("Segoe UI", 9)).pack(side="left", padx=12)
        self.text = tk.Text(self, wrap="word", bg=self.COLORS["bg"], fg=self.COLORS["fg"],
                            font=("Segoe UI", 16), relief="flat", padx=14, pady=10, height=5,
                            highlightthickness=0, state="disabled", cursor="arrow")
        self.text.pack(side="top", fill="both", expand=True)
        for track in (0, 1):
            self.text.tag_configure(f"speaker{track}", foreground=self.COLORS[track],
                                    font=("Segoe UI", 16, "bold"))
        self.text.tag_configure("draft", foreground=self.COLORS["draft"])
        self.protocol("WM_DELETE_WINDOW", app._close_caption_window)

    def _apply_top(self) -> None:
        self.attributes("-topmost", bool(self.on_top.get()))

    def set_status(self, text: str) -> None:
        self.status.config(text=text)
        if not self.captions and text:
            self._render()

    def show(self, caption: Caption) -> None:
        if caption.final and not caption.text:
            self.captions.pop(caption.id, None)  # silence or echo: drop the line
        else:
            self.captions[caption.id] = caption
        # Keep only the most recent lines.
        for cid in sorted(self.captions, key=lambda i: self.captions[i].started)[:-self.MAX_LINES]:
            del self.captions[cid]
        self.status.config(text="")
        self._render()

    def _render(self) -> None:
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        ordered = sorted(self.captions.values(), key=lambda c: c.started)
        if not ordered:
            self.text.insert("end", self.status.cget("text"), "draft")
        previous = None
        for c in ordered:
            if previous is not None:
                self.text.insert("end", "\n")
            if c.track != previous:
                self.text.insert("end", f"{c.speaker}: ", f"speaker{c.track}")
            self.text.insert("end", c.text, () if c.final else ("draft",))
            previous = c.track
        self.text.see("end")
        self.text.config(state="disabled")


class SettingsDialog:
    def __init__(self, app: App):
        self.app = app
        s = app.settings
        win = self.win = tk.Toplevel(app.root)
        win.title("Settings")
        win.transient(app.root)
        win.grab_set()
        f = ttk.Frame(win, padding=14)
        f.pack(fill="both", expand=True)

        try:
            devices = list_devices()
        except Exception:
            devices = {"microphones": [], "speakers": []}
        default = "(Windows default)"

        self.vars: dict[str, tk.Variable] = {}
        row = 0

        def add(label: str, widget_factory, key: str, value) -> None:
            nonlocal row
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=value)
            self.vars[key] = var
            widget_factory(var).grid(row=row, column=1, sticky="we", pady=3, padx=(8, 0))
            row += 1

        def entry(var):
            return ttk.Entry(f, textvariable=var, width=42)

        def combo(options, readonly=True):
            return lambda var: ttk.Combobox(f, textvariable=var, values=options, width=40,
                                            state="readonly" if readonly else "normal")

        def check(var):
            return ttk.Checkbutton(f, variable=var)

        add("Meetings folder", entry, "output_dir", s.output_dir)
        ttk.Button(f, text="Browse...", command=self._browse).grid(row=row - 1, column=2, padx=4)
        add("Your name in transcripts", entry, "your_name", s.your_name)
        add("Label for other people", entry, "others_label", s.others_label)
        add("Microphone", combo([default] + devices["microphones"]), "mic_device", s.mic_device or default)
        add("Speakers / headset", combo([default] + devices["speakers"]), "speaker_device",
            s.speaker_device or default)
        add("Whisper model", combo(WHISPER_MODELS), "whisper_model", s.whisper_model)
        add("Run Whisper on", combo(["auto", "cpu", "cuda"]), "whisper_device", s.whisper_device)
        add("Language (blank = auto)", combo(["", "en", "es", "fr", "de", "pt", "it", "nl", "ja", "zh"],
                                             readonly=False), "language", s.language or "")
        add("Offer to record when a meeting app uses the mic", check, "auto_detect", s.auto_detect)
        add("Stop when the meeting app releases the mic", check, "auto_stop", s.auto_stop)
        add("Transcribe right after recording", check, "auto_process", s.auto_process)
        add("Show live captions when recording starts", check, "live_captions", s.live_captions)
        add("Live caption model", combo(["tiny", "base", "small"]), "live_caption_model",
            s.live_caption_model)
        add("Remind me to tell participants I'm recording", check, "consent_reminder",
            s.consent_reminder)
        add("Keep running in the Dock when the window is closed" if IS_MAC else
            "Keep running in the tray when the window is closed", check, "close_to_tray",
            s.close_to_tray)
        if startup.supported():
            add(f"{startup.label()} (opens hidden)", check, "start_with_windows",
                startup.is_enabled())
        f.columnconfigure(1, weight=1)

        ttk.Label(f, foreground="gray", wraplength=460, justify="left", text=(
            "Model sizes: tiny/base are fast but rough. small is a good default on CPU. "
            "medium and large-v3 are the most accurate and want a GPU.")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        row += 1
        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=lambda: self._save(default)).pack(side="right", padx=6)

    def _browse(self) -> None:
        path = filedialog.askdirectory(parent=self.win, initialdir=self.vars["output_dir"].get())
        if path:
            self.vars["output_dir"].set(path)

    def _save(self, default: str) -> None:
        s = self.app.settings
        v = {k: var.get() for k, var in self.vars.items()}
        s.output_dir = v["output_dir"].strip() or s.output_dir
        s.your_name = v["your_name"].strip() or "Me"
        s.others_label = v["others_label"].strip() or "Others"
        s.mic_device = None if v["mic_device"] == default else v["mic_device"]
        s.speaker_device = None if v["speaker_device"] == default else v["speaker_device"]
        s.whisper_model = v["whisper_model"]
        s.whisper_device = v["whisper_device"]
        s.language = v["language"].strip() or None
        s.auto_detect = bool(v["auto_detect"])
        s.auto_stop = bool(v["auto_stop"])
        s.auto_process = bool(v["auto_process"])
        s.close_to_tray = bool(v["close_to_tray"])
        s.consent_reminder = bool(v["consent_reminder"])
        s.live_captions = bool(v["live_captions"])
        s.live_caption_model = v["live_caption_model"]
        if "start_with_windows" in v and bool(v["start_with_windows"]) != startup.is_enabled():
            try:
                startup.set_enabled(bool(v["start_with_windows"]))
            except OSError as exc:
                messagebox.showerror("Start with Windows", f"Couldn't change it: {exc}", parent=self.win)
        s.save()
        self.win.destroy()
        self.app.refresh_list()
        self.app.set_status("Settings saved.")


def _redirect_missing_streams() -> None:
    """Give a windowed process somewhere to write.

    Without a console (the .exe build, or pythonw) sys.stdout and sys.stderr are None,
    and the Whisper model download crashes when its progress bar writes to them.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_path = settings_dir() / "app.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def _set_icon(root: tk.Tk) -> None:
    icon = Path(__file__).parent / "assets" / "icon.ico"
    if sys.platform == "win32" and icon.exists():
        try:
            root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    _redirect_missing_streams()
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = App()
    if "--minimized" in argv:
        # Started at sign-in: go straight to the tray, or the taskbar if there's no tray.
        if app.can_hide:
            app.root.withdraw()
        else:
            app.root.iconify()
    app.run()
    # The window is gone and any recording was stopped in quit(). Exit now rather than
    # wait on the tray library's thread, which can linger on some systems.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(0)
