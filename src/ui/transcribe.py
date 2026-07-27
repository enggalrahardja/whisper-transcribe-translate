import os
import queue
import threading
import time

import customtkinter as ctk
from customtkinter import filedialog as fd
from mutagen import File, MutagenError
from pydub import AudioSegment, exceptions
from tkinterdnd2 import TkinterDnD, DND_ALL
from ..whisper.utils import get_writer
from .ctkAlert import CTkAlert
from .ctkLoader import CTkLoader
from .ctkdropdown import CTkScrollableDropdownFrame
from .icons import icons
from .style import FONTS, DROPDOWN
from ..logic import Transcriber
from ..logic.settings import SettingsHandler
from ..logic.gpu_details import GPUInfo


class CTk(ctk.CTkFrame, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


class TranscribeUI(CTk):
    def __init__(self, parent):
        super().__init__(master=parent, width=620, height=720, fg_color=("#F2F0EE", "#1E1F22"), border_width=0)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(self, text=" Transcribe Audio", font=FONTS["title"], image=icons["audio_file"],
                             compound="left")
        title.grid(row=0, column=0, padx=20, pady=20, sticky="w")

        self.close_btn = ctk.CTkButton(self, text="", image=icons["close"], fg_color="transparent", hover=False, width=30,
                                  height=30, command=self.hide_transcribe_ui)
        self.close_btn.grid(row=0, column=1, padx=20, pady=20, sticky="e")

        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=1, column=0, padx=20, pady=20, sticky="nsew", columnspan=2)
        self.main_frame.grid_columnconfigure(0, weight=1)

        self.master = parent
        self.drag_drop = None
        self.audio_path = None
        self.cancel_signal = False
        self.loader = None
        self.option_menu = None
        self.queue = queue.Queue()
        self.progress_label = None
        self.log_textbox = None

        self.default_widget()

        self.grid(row=0, column=0, sticky="nsew")

    def default_widget(self):
        label = ctk.CTkLabel(self.main_frame, text="No File Selected", font=FONTS["subtitle_bold"])
        label.grid(row=0, column=0, padx=0, pady=(20, 5), sticky="w")

        self.drag_drop = ctk.CTkButton(self.main_frame, text="➕ \nDrag & Drop Here", width=500, height=250,
                                       text_color=("#000000", "#DFE1E5"), hover=False, fg_color="transparent",
                                       border_width=2, corner_radius=5, border_color=("#D3D5DB", "#2B2D30"),
                                       font=FONTS["normal"])
        self.drag_drop.grid(row=1, column=0, padx=0, pady=10, sticky="nsew")

        self.drag_drop.drop_target_register(DND_ALL)
        self.drag_drop.dnd_bind('<<Drop>>', self.drop)

        label_or = ctk.CTkLabel(self.main_frame, text="Or", font=("", 14))
        label_or.grid(row=2, column=0, padx=0, pady=5, sticky="nsew")

        select_btn = ctk.CTkButton(self.main_frame, text="Browse Files", width=150, height=40,
                                   command=self.select_file_callback, font=FONTS["normal"])
        select_btn.grid(row=3, column=0, padx=200, pady=10, sticky="nsew")

        label_1 = ctk.CTkLabel(self.main_frame, text="Support Formats: WAV, MP3, OGG, FLAC, MP4, MOV, WMV, AVI",
                               fg_color=("#D3D5DB", "#2B2D30"), corner_radius=5, width=400, height=50,
                               font=FONTS["small"])
        label_1.grid(row=4, column=0, padx=0, pady=20, sticky="sew")

    def task_widget(self):
        file_name, duration, file_size = self.get_audio_info(self.audio_path)

        # Get model and hardware info
        settings_handler = SettingsHandler()
        settings = settings_handler.load_settings()
        model_size = settings.get("model_size", "base").upper()
        device = settings.get("device", "cpu").upper()

        gpu_info = GPUInfo()
        hw_info = gpu_info.get_gpu_info()

        if device == "GPU" and hw_info["cuda_available"]:
            device_name = hw_info["gpu_name"]
            device_display = f"GPU ({device_name})"
        else:
            device_display = "CPU"

        label = ctk.CTkLabel(self.main_frame, text="Selected File", font=FONTS["subtitle_bold"])
        label.grid(row=0, column=0, padx=0, pady=(20, 5), sticky="w")

        frame = ctk.CTkFrame(self.main_frame)
        frame.grid(row=1, column=0, padx=0, pady=10, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)

        label_1 = ctk.CTkLabel(frame, text="File Name", font=FONTS["normal"])
        label_1.grid(row=0, column=0, padx=20, pady=(20, 5), sticky="w")
        label_1_value = ctk.CTkLabel(frame, text=file_name, font=FONTS["small"])
        label_1_value.grid(row=0, column=1, padx=20, pady=(20, 5), sticky="e")

        label_2 = ctk.CTkLabel(frame, text="Duration", font=FONTS["normal"])
        label_2.grid(row=1, column=0, padx=20, pady=5, sticky="w")
        label_2_value = ctk.CTkLabel(frame, text=duration, font=FONTS["small"])
        label_2_value.grid(row=1, column=1, padx=20, pady=5, sticky="e")

        label_3 = ctk.CTkLabel(frame, text="Size", font=FONTS["normal"])
        label_3.grid(row=2, column=0, padx=20, pady=(5, 20), sticky="w")
        label_3_value = ctk.CTkLabel(frame, text=f"{file_size:.2f} MB", font=FONTS["small"])
        label_3_value.grid(row=2, column=1, padx=20, pady=(5, 20), sticky="e")

        # Model and Hardware Info Section
        info_label = ctk.CTkLabel(self.main_frame, text="Model & Hardware Info", font=FONTS["subtitle_bold"])
        info_label.grid(row=2, column=0, padx=0, pady=(10, 5), sticky="w")

        info_frame = ctk.CTkFrame(self.main_frame, fg_color=("#D3D5DB", "#2B2D30"))
        info_frame.grid(row=3, column=0, padx=0, pady=10, sticky="nsew")
        info_frame.grid_columnconfigure(0, weight=1)

        model_label = ctk.CTkLabel(info_frame, text="Model", font=FONTS["normal"])
        model_label.grid(row=0, column=0, padx=20, pady=(15, 5), sticky="w")
        model_value = ctk.CTkLabel(info_frame, text=model_size, font=FONTS["small"], text_color=("#1E88E5", "#64B5F6"))
        model_value.grid(row=0, column=1, padx=20, pady=(15, 5), sticky="e")

        device_label = ctk.CTkLabel(info_frame, text="Device", font=FONTS["normal"])
        device_label.grid(row=1, column=0, padx=20, pady=(5, 15), sticky="w")
        device_value = ctk.CTkLabel(info_frame, text=device_display, font=FONTS["small"], text_color=("#1E88E5", "#64B5F6"))
        device_value.grid(row=1, column=1, padx=20, pady=(5, 15), sticky="e")

        start_btn = ctk.CTkButton(self.main_frame, text="Start Transcribing", height=40, command=self.start_callback,
                                  font=FONTS["normal"])
        start_btn.grid(row=4, column=0, padx=200, pady=20, sticky="nsew")

    def result_widget(self):
        result = self.queue.get()
        text = str(result["text"]).strip()

        # Clear all previous widgets
        widgets = self.main_frame.winfo_children()
        for widget in widgets:
            widget.destroy()

        result_label = ctk.CTkLabel(self.main_frame, text="Transcribed Text", font=FONTS["subtitle_bold"])
        result_label.grid(row=0, column=0, padx=10, pady=(20, 10), sticky="w")

        textbox = ctk.CTkTextbox(self.main_frame, width=580, height=200, border_width=2, font=FONTS["normal"])
        textbox.grid(row=1, column=0, padx=10, pady=(0, 20), sticky="nsew")
        textbox.insert("0.0", text=text)

        download_label = ctk.CTkLabel(self.main_frame, text="Download Text and Subtitles", font=FONTS["subtitle_bold"])
        download_label.grid(row=2, column=0, padx=10, pady=(10, 10), sticky="w")

        self.option_menu = ctk.CTkOptionMenu(self.main_frame, width=200, height=35)
        self.option_menu.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="w")
        format_values = ["Text File (txt)",
                         "Subtitles (SRT)",
                         "WebVTT (VTT)",
                         "Tab-Separated Values (TSV)",
                         "JSON File (json)",
                         "Save as all extensions"]
        CTkScrollableDropdownFrame(self.option_menu, values=format_values, **DROPDOWN)
        self.option_menu.set(format_values[0])

        download_btn = ctk.CTkButton(self.main_frame, text="Download", command=lambda: self.save_text(result),
                                     font=FONTS["normal"], height=40, width=150)
        download_btn.grid(row=4, column=0, padx=10, pady=(0, 20), sticky="w")

    def save_text(self, result):
        file_name = os.path.basename(self.audio_path)
        sep = "."
        file_name = file_name.split(sep, 1)[0]

        selected_extension = self.option_menu.get()

        file_extension_map = {
            "Text File (txt)": ".txt",
            "Subtitles (SRT)": ".srt",
            "WebVTT (VTT)": ".vtt",
            "Tab-Separated Values (TSV)": ".tsv",
            "JSON File (json)": ".json",
            "Save as all extensions": ".all",
        }

        file_extension = file_extension_map[selected_extension]

        file_path = fd.asksaveasfilename(
            parent=self,
            initialfile=file_name,
            title="Export subtitle",
            defaultextension=file_extension,
            filetypes=[(f"{selected_extension} Files", "*" + file_extension)]
        )

        if file_path:
            dir_name, get_file_name = os.path.split(file_path)

            if file_extension == ".srt":
                writer = get_writer("srt", dir_name)
                writer(result, self.audio_path, {"highlight_words": True, "max_line_count": 50, "max_line_width": 3})
            elif file_extension == ".txt":
                txt_writer = get_writer("txt", dir_name)
                txt_writer(result, self.audio_path, {"highlight_words": True, "max_line_count": 50,
                                                     "max_line_width": 3})
            elif file_extension == ".vtt":
                vtt_writer = get_writer("vtt", dir_name)
                vtt_writer(result, self.audio_path, {"highlight_words": True, "max_line_count": 50,
                                                     "max_line_width": 3})
            elif file_extension == ".tsv":
                tsv_writer = get_writer("tsv", dir_name)
                tsv_writer(result, self.audio_path, {"highlight_words": True, "max_line_count": 50,
                                                     "max_line_width": 3})
            elif file_extension == ".json":
                json_writer = get_writer("json", dir_name)
                json_writer(result, self.audio_path, {"highlight_words": True, "max_line_count": 50,
                                                      "max_line_width": 3})
            elif file_extension == ".all":
                all_writer = get_writer("all", dir_name)
                all_writer(result, self.audio_path, {"highlight_words": True, "max_line_count": 50,
                                                     "max_line_width": 3})

    def start_callback(self):
        self.close_btn.configure(state="disabled")
        widgets = self.main_frame.winfo_children()
        for widget in widgets:
            widget.destroy()

        # Create progress UI
        progress_label = ctk.CTkLabel(self.main_frame, text="Transcription Progress", font=FONTS["subtitle_bold"])
        progress_label.grid(row=0, column=0, padx=0, pady=(20, 10), sticky="w")

        self.progress_label = ctk.CTkLabel(self.main_frame, text="Initializing... 0%", font=FONTS["normal"])
        self.progress_label.grid(row=1, column=0, padx=0, pady=(0, 10), sticky="w")

        # Progress bar - changed to determinate mode
        self.progress_bar = ctk.CTkProgressBar(self.main_frame, width=580, mode="determinate")
        self.progress_bar.grid(row=2, column=0, padx=0, pady=(0, 20), sticky="ew")
        self.progress_bar.set(0)

        # Log textbox
        log_label = ctk.CTkLabel(self.main_frame, text="Process Log", font=FONTS["subtitle_bold"])
        log_label.grid(row=3, column=0, padx=0, pady=(10, 10), sticky="w")

        self.log_textbox = ctk.CTkTextbox(self.main_frame, width=580, height=200, font=FONTS["small"], border_width=2)
        self.log_textbox.grid(row=4, column=0, padx=0, pady=(0, 20), sticky="nsew")
        self.log_textbox.configure(state="disabled")

        cancel_btn = ctk.CTkButton(self.main_frame, text="Cancel", height=40, width=150, command=self.set_signal,
                                   font=FONTS["normal"], fg_color="#E53935", hover_color="#C62828")
        cancel_btn.grid(row=5, column=0, padx=0, pady=(0, 20), sticky="w")

        self.add_log("Starting transcription process...")
        thread = threading.Thread(target=self.start_transcribing, args=(self.audio_path, self.check_signal))
        thread.start()

    def start_transcribing(self, audio_path, check_signal):
        try:
            self.update_progress("Loading model...", 0)
            self.add_log("Loading Whisper model...")

            transcriber = Transcriber(audio=audio_path)

            self.update_progress("Model loaded. Processing audio...", 5)
            self.add_log(f"Model loaded successfully")
            self.add_log(f"Processing audio file: {os.path.basename(audio_path)}")

            result = transcriber.audio_recognition(cancel_func=check_signal, progress_callback=self.on_progress)

            if result:
                self.update_progress("Transcription completed!", 100)
                self.add_log("Transcription completed successfully")
                self.queue.put(result)
                self.close_btn.configure(state="normal")
                self.after(1000, self.result_widget)
            else:
                self.add_log("Transcription cancelled by user")
                self.close_btn.configure(state="normal")
                self.after(1000, self.default_widget)
        except Exception as e:
            self.add_log(f"Error: {str(e)}")
            self.update_progress("Error occurred", 0)
            CTkAlert(parent=self.master, status="error", title="Error", msg=f"Transcription failed: {str(e)}")
            self.close_btn.configure(state="normal")
            self.after(1000, self.default_widget)

    def set_signal(self):
        self.cancel_signal = True

    def check_signal(self):
        original_value = self.cancel_signal

        if self.cancel_signal:
            self.cancel_signal = False
            self.add_log("Cancelling transcription...")
            self.close_btn.configure(state="normal")
            self.after(1000, self.default_widget)

        return original_value

    def on_progress(self, percentage):
        """Callback function to update progress from transcriber"""
        self.update_progress(f"Processing audio... {percentage}%", percentage)
        if percentage % 10 == 0:  # Log every 10%
            self.add_log(f"Progress: {percentage}% completed")

    def update_progress(self, message, percentage=None):
        """Update progress label and bar with current status"""
        if self.progress_label:
            if percentage is not None:
                self.progress_label.configure(text=f"{message}")
                if self.progress_bar:
                    self.progress_bar.set(percentage / 100.0)
            else:
                self.progress_label.configure(text=message)

    def add_log(self, message):
        """Add a log entry to the log textbox"""
        if self.log_textbox:
            timestamp = time.strftime("%H:%M:%S")
            self.log_textbox.configure(state="normal")
            self.log_textbox.insert("end", f"[{timestamp}] {message}\n")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")

    def select_file_callback(self):
        file_path = fd.askopenfilename(
            filetypes=[("Audio files", "*.mp3 *.wav *.ogg *.flac"), ("Video files", "*.mp4 *.mov *.wmv *.avi")])
        if file_path:
            audio_path = os.path.abspath(file_path)
            if self.is_streamable_audio(audio_path):
                self.audio_path = audio_path
                widgets = self.main_frame.winfo_children()

                for widget in widgets:
                    widget.destroy()
                self.after(1000, self.task_widget)
            else:
                CTkAlert(parent=self.master, status="error", title="Error",
                         msg="The chosen audio file is not valid or streamable.")

    @staticmethod
    def is_streamable_audio(audio_path):
        if not os.path.isfile(audio_path):
            return False

        try:
            audio = AudioSegment.from_file(audio_path)
            audio_info = File(audio_path)
            return len(audio) > 0 and audio_info.info.length > 0
        except (FileNotFoundError, exceptions.CouldntDecodeError, MutagenError):
            return False

    @staticmethod
    def get_audio_info(file_path):
        try:
            if not os.path.isfile(file_path):
                return None, None

            file_name = os.path.basename(file_path)

            audio_info = File(file_path)
            if audio_info is None:
                return None, None

            duration_seconds = audio_info.info.length

            duration_formatted = time.strftime("%H:%M:%S", time.gmtime(duration_seconds))

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

            return file_name, duration_formatted, file_size_mb
        except MutagenError:
            return None, None

    def drop(self, event):
        dropped_file = event.data.replace("{", "").replace("}", "")
        audio_path = os.path.abspath(dropped_file)
        if self.is_streamable_audio(audio_path):
            self.audio_path = audio_path
            widgets = self.main_frame.winfo_children()

            for widget in widgets:
                widget.destroy()

            self.after(1000, self.task_widget)
        else:
            CTkAlert(parent=self.master, status="error", title="Error",
                     msg="The chosen audio file is not valid or streamable.")

    def hide_transcribe_ui(self):
        self.destroy()
