import os

import customtkinter as ctk
from PIL import Image

from src.logic.settings import SettingsHandler
from src.ui.add_subtitles import AddSubtitlesUI
from src.ui.live_transcribe import LiveTranscribeUI
from src.ui.settings import SettingsUI
from src.ui.style import FONTS
from src.ui.transcribe import TranscribeUI
from src.ui.translate import TranslateUI

current_path = os.path.dirname(os.path.realpath(__file__))
icon_path = f"{current_path}{os.path.sep}assets{os.path.sep}icons{os.path.sep}"

icons = {
    "logo": ctk.CTkImage(dark_image=Image.open(f"{icon_path}logo.png"),
                         light_image=Image.open(f"{icon_path}logo.png"), size=(50, 50)),
    "close": ctk.CTkImage(dark_image=Image.open(f"{icon_path}close_dark.png"),
                          light_image=Image.open(f"{icon_path}close_light.png"), size=(30, 30)),
    "audio_file": ctk.CTkImage(dark_image=Image.open(f"{icon_path}audio_file_dark.png"),
                               light_image=Image.open(f"{icon_path}audio_file_light.png"),
                               size=(30, 30)),
    "translation": ctk.CTkImage(dark_image=Image.open(f"{icon_path}translation_dark.png"),
                                light_image=Image.open(f"{icon_path}translation_light.png"),
                                size=(30, 30)),
    "microphone": ctk.CTkImage(dark_image=Image.open(f"{icon_path}microphone_dark.png"),
                               light_image=Image.open(f"{icon_path}microphone_light.png"),
                               size=(30, 30)),
    "subtitles": ctk.CTkImage(dark_image=Image.open(f"{icon_path}subtitles_dark.png"),
                              light_image=Image.open(f"{icon_path}subtitles_light.png"),
                              size=(30, 30)),
    "settings": ctk.CTkImage(dark_image=Image.open(f"{icon_path}settings_dark.png"),
                             light_image=Image.open(f"{icon_path}settings_light.png"),
                             size=(30, 30))
}

logo = f"{icon_path}logo.ico"

btn = {
    "width": 280,
    "height": 116,
    "text_color": ("#FFFFFF", "#DFE1E5"),
    "compound": "left",
    "font": ("Inter", 16)
}

class Testing(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.geometry("620x720")
        self.resizable(False, False)
        # Use iconphoto for cross-platform compatibility
        try:
            icon_image = Image.open(f"{icon_path}logo.png")
            self.iconphoto(True, icon_image)
        except Exception:
            pass  # Silently fail if icon can't be loaded
        self.title("Whisper Transcriber")

        settings_handler = SettingsHandler()
        settings = settings_handler.load_settings()
        theme = settings.get("theme")
        color_theme = settings.get("color_theme")

        ctk.set_appearance_mode(theme)
        ctk.set_default_color_theme(color_theme)

        self.main_ui()

    def main_ui(self):
        title = ctk.CTkLabel(self, text="Welcome to Whisper Transcriber", text_color=("#000000", "#DFE1E5"),
                             font=FONTS["title_bold"], image=icons["logo"], compound="top")
        title.grid(row=0, column=0, padx=20, pady=20, sticky="nsew", columnspan=2)

        label = ctk.CTkLabel(self, text="Select a Service", text_color=("#000000", "#DFE1E5"), font=FONTS["subtitle"])
        label.grid(row=1, column=0, padx=20, pady=20, sticky="w")

        btn_1 = ctk.CTkButton(self, text="Transcribe Audio", **btn, image=icons["audio_file"],
                              command=lambda: TranscribeUI(parent=self))
        btn_1.grid(row=2, column=0, padx=(20, 10), pady=10, sticky="nsew")
        btn_2 = ctk.CTkButton(self, text="Translate Audio", **btn, image=icons["translation"],
                              command=lambda: TranslateUI(parent=self))
        btn_2.grid(row=2, column=1, padx=(10, 20), pady=10, sticky="nsew")

        btn_3 = ctk.CTkButton(self, text="Live Transcriber", **btn, image=icons["microphone"],
                              command=lambda: LiveTranscribeUI(parent=self))
        btn_3.grid(row=3, column=0, padx=(20, 10), pady=10, sticky="nsew")
        btn_4 = ctk.CTkButton(self, text="Add Subtitle", **btn, image=icons["subtitles"],
                              command=lambda: AddSubtitlesUI(parent=self))
        btn_4.grid(row=3, column=1, padx=(10, 20), pady=10, sticky="nsew")

        btn_5 = ctk.CTkButton(self, text="Settings", font=("Inter", 16), text_color=("#FFFFFF", "#DFE1E5"), width=280,
                              height=100, image=icons["settings"], command=lambda: SettingsUI(parent=self))
        btn_5.grid(row=4, column=0, padx=20, pady=20, sticky="nsew", columnspan=2)


app = Testing()
app.mainloop()
