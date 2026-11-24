#!/usr/bin/env python3
"""
SceneMerge - By Baraa Hawamdeh
Standalone single-file GUI that normalizes and merges video clips into a single video
with optional blurred background for mismatched aspect ratios, using FFmpeg.

Prereqs:
    pip install FreeSimpleGUI
    ffmpeg and ffprobe available in PATH (or next to script, or set FFMPEG/FFPROBE)

Build to EXE:
    pip install pyinstaller
    pyinstaller --noconsole --onefile --name SceneMerge SceneMerge.py
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import shutil
import threading
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any

import FreeSimpleGUI as sg

# ---------------- Locate Binaries ----------------

def resource_path(relative: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller."""
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__ if '__file__' in globals() else sys.argv[0])))
    return os.path.join(base_dir, relative)


def resolve_binary(binary_name: str, env_var: str) -> Optional[str]:
    """
    Search for executable in order:
    1. Bundled next to executable (sys.executable) or inside PyInstaller bundle (sys._MEIPASS).
    2. System PATH via shutil.which.
    3. Environment variable if set and exists.
    """
    exe_name = f"{binary_name}.exe" if sys.platform == "win32" else binary_name

    # 1. Bundled next to the executable or inside the bundle
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        cand = os.path.join(exe_dir, exe_name)
        if os.path.isfile(cand):
            return cand
        cand_bin = os.path.join(exe_dir, "bin", exe_name)
        if os.path.isfile(cand_bin):
            return cand_bin

    meipass_cand = resource_path(exe_name)
    if os.path.isfile(meipass_cand):
        return meipass_cand

    meipass_bin = resource_path(os.path.join("bin", exe_name))
    if os.path.isfile(meipass_bin):
        return meipass_bin

    if not getattr(sys, 'frozen', False):
        script_dir = os.path.dirname(os.path.abspath(__file__ if '__file__' in globals() else sys.argv[0]))
        cand = os.path.join(script_dir, exe_name)
        if os.path.isfile(cand):
            return cand
        cand_bin = os.path.join(script_dir, "bin", exe_name)
        if os.path.isfile(cand_bin):
            return cand_bin

    # 2. System PATH
    found = shutil.which(binary_name)
    if found:
        return found

    # 3. Environment variable
    env_val = os.environ.get(env_var, "").strip()
    if env_val and os.path.isfile(env_val):
        return env_val

    return None


def prompt_for_binary(binary_name: str) -> Optional[str]:
    """Prompt user with a file dialog to locate the binary manually."""
    exe_name = f"{binary_name}.exe" if sys.platform == "win32" else binary_name
    msg = (
        f"'{exe_name}' could not be found automatically.\n\n"
        f"Please locate '{exe_name}' on your computer to continue."
    )
    sg.popup(msg, title=f"Locate {exe_name}")
    chosen = sg.popup_get_file(
        f"Select {exe_name}",
        file_types=(
            (f"{binary_name.upper()} Executable", f"*{os.path.splitext(exe_name)[1] if os.path.splitext(exe_name)[1] else '*'}")
            if sys.platform == "win32" else ("Executable", "*"),
            ("All Files", "*.*")
        ),
        title=f"Browse for {exe_name}",
        no_window=False
    )
    if chosen and os.path.isfile(chosen):
        return chosen
    return None


def terminate_process_tree(proc: subprocess.Popen) -> None:
    """Terminate process and any child processes safely across platforms."""
    if not proc or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


FFMPEG = resolve_binary("ffmpeg", "FFMPEG")
FFPROBE = resolve_binary("ffprobe", "FFPROBE")

# ---------------- Configuration & Constants ----------------
TEMP_ROOT = os.path.join(tempfile.gettempdir(), "scenemerge_temp")
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".ts", ".m4v", ".avi", ".webm", ".flv", ".wmv"}

APP_NAME = "SceneMerge"
__version__ = "1.0.0"
AUTHOR = "By Baraa Hawamdeh"
APP_TITLE = f"{APP_NAME} v{__version__} — {AUTHOR}"

# Global process tracking and cancel flag
current_process: Optional[subprocess.Popen] = None
process_lock = threading.Lock()
cancel_requested = False
merge_worker_thread: Optional[threading.Thread] = None

# ---------------- Utilities ----------------

def ensure_temp_dir() -> str:
    os.makedirs(TEMP_ROOT, exist_ok=True)
    return TEMP_ROOT


def format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS string."""
    if seconds <= 0:
        return "00:00"
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def parse_time_to_seconds(t_str: str) -> float:
    """Support HH:MM:SS.xx or MM:SS.xx or SS."""
    t_str = t_str.strip()
    if not t_str:
        return 0.0
    parts = t_str.split(":")
    try:
        parts = [float(p) for p in parts]
    except Exception:
        return 0.0
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60.0 + parts[1]
    return parts[-3] * 3600.0 + parts[-2] * 60.0 + parts[-1]


def probe_video_metadata(path: str) -> Dict[str, Any]:
    """Query video details with ffprobe: duration, width, height, fps, has_audio."""
    meta: Dict[str, Any] = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 25.0,
        "has_audio": False,
        "error": None
    }
    if not FFPROBE:
        meta["error"] = "ffprobe not found"
        return meta

    try:
        # Run ffprobe to get stream and format info
        cmd = [
            FFPROBE,
            "-v", "error",
            "-show_entries", "stream=codec_type,width,height,r_frame_rate,duration:format=duration",
            "-of", "default=noprint_wrappers=1",
            path
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        if res.returncode != 0:
            meta["error"] = res.stderr.strip() or "ffprobe failed"
            return meta

        v_found = False
        format_duration = 0.0

        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()

            if k == "codec_type":
                if v == "audio":
                    meta["has_audio"] = True
                elif v == "video":
                    v_found = True
            elif k == "width" and not meta["width"]:
                try:
                    meta["width"] = int(v)
                except ValueError:
                    pass
            elif k == "height" and not meta["height"]:
                try:
                    meta["height"] = int(v)
                except ValueError:
                    pass
            elif k == "r_frame_rate" and not meta["fps"]:
                try:
                    if "/" in v:
                        num, den = v.split("/", 1)
                        den_val = float(den)
                        if den_val > 0:
                            meta["fps"] = round(float(num) / den_val, 2)
                    else:
                        meta["fps"] = round(float(v), 2)
                except Exception:
                    pass
            elif k == "duration":
                try:
                    dur_val = float(v)
                    if dur_val > meta["duration"]:
                        meta["duration"] = dur_val
                except ValueError:
                    pass

        # Fallback duration probe if format duration was missed
        if meta["duration"] <= 0:
            cmd2 = [
                FFPROBE,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path
            ]
            res2 = subprocess.run(
                cmd2,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            try:
                meta["duration"] = float(res2.stdout.strip())
            except Exception:
                pass

    except Exception as e:
        meta["error"] = str(e)

    return meta


# ---------------- Filtergraph Generation ----------------

def build_filter_complex_script(
    items: List[Dict[str, Any]],
    target_w: int,
    target_h: int,
    target_fps: float,
    enable_blur: bool,
    blur_strength: int
) -> str:
    """
    Build complete FFmpeg filter_complex script content.
    Each clip is normalized:
      - Video: setsar=1, format=yuv420p, fps=target_fps, and scaled/padded or scaled/blurred.
      - Audio: aresample=48000, aformat=sample_fmts=fltp:channel_layouts=stereo.
        If a clip lacks audio, generate silent audio matching the clip's duration.
    Concatenate all items at the end with concat=n=N:v=1:a=1[outv][outa].
    """
    lines: List[str] = []
    n = len(items)
    target_ar = target_w / target_h

    # Track inputs for concat
    concat_inputs: List[str] = []

    for i, item in enumerate(items):
        meta = item["meta"]
        src_w = meta.get("width", 0) or target_w
        src_h = meta.get("height", 0) or target_h
        src_ar = src_w / src_h if src_h > 0 else target_ar
        dur = meta.get("duration", 0.0)
        has_audio = meta.get("has_audio", False)

        # Aspect ratios are considered matching if difference is negligible (< 0.01)
        ar_matches = abs(src_ar - target_ar) < 0.01

        # --- Video Processing ---
        if enable_blur and not ar_matches:
            # Blurred background strategy:
            # 1. Split stream into foreground and background.
            # 2. Background: scale to completely cover output frame (max ratio) and crop to target_w:target_h,
            #    then apply boxblur or gblur.
            # 3. Foreground: scale to fit inside output frame (maintaining aspect ratio).
            # 4. Overlay centered foreground on blurred background.
            # 5. Set SAR=1, format=yuv420p, fps.
            blur_sigma = max(5, int(blur_strength))
            v_chain = (
                f"[{i}:v]split=2[fg_raw_{i}][bg_raw_{i}];"
                f"[bg_raw_{i}]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},"
                f"boxblur=luma_radius={blur_sigma}:luma_power=2[bg_{i}];"
                f"[fg_raw_{i}]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg_{i}];"
                f"[bg_{i}][fg_{i}]overlay=(W-w)/2:(H-h)/2,"
                f"fps={target_fps},setsar=1,format=yuv420p[v{i}]"
            )
        else:
            # Standard scale-to-fit with black padding (or exact scale if aspect ratio matches)
            if ar_matches:
                v_chain = (
                    f"[{i}:v]scale={target_w}:{target_h},"
                    f"fps={target_fps},setsar=1,format=yuv420p[v{i}]"
                )
            else:
                v_chain = (
                    f"[{i}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                    f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"fps={target_fps},setsar=1,format=yuv420p[v{i}]"
                )

        lines.append(v_chain)

        # --- Audio Processing ---
        if has_audio:
            # Resample and format audio to 48000 Hz stereo
            a_chain = f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        else:
            # Generate silence for this clip's duration
            safe_dur = max(0.1, dur)
            a_chain = (
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=duration={safe_dur:.3f}[a{i}]"
            )

        lines.append(a_chain)
        concat_inputs.append(f"[v{i}][a{i}]")

    # Final Concat node
    concat_chain = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]"
    lines.append(concat_chain)

    return ";\n".join(lines)


# ---------------- Worker Thread for Merging ----------------

def merge_worker(
    items: List[Dict[str, Any]],
    output_path: str,
    target_w: int,
    target_h: int,
    target_fps: float,
    crf: int,
    preset: str,
    enable_blur: bool,
    blur_strength: int,
    total_duration: float,
    window: sg.Window
) -> None:
    global current_process, cancel_requested
    cancel_requested = False

    filter_script_path: Optional[str] = None
    start_time = time.time()

    try:
        ensure_temp_dir()
        script_file_name = f"filter_script_{int(time.time())}_{os.getpid()}.txt"
        filter_script_path = os.path.join(TEMP_ROOT, script_file_name)

        window.write_event_value("-LOG_LINE-", f"[START] Preparing merge for {len(items)} clip(s)...")
        if len(items) == 1:
            window.write_event_value("-LOG_LINE-", "[INFO] Single clip selected. Normalizing resolution/framerate and applying settings.")

        window.write_event_value("-LOG_LINE-", f"[CONFIG] Output Resolution: {target_w}x{target_h} | FPS: {target_fps}")
        window.write_event_value("-LOG_LINE-", f"[CONFIG] Quality: CRF {crf} | Preset {preset} | Blurred BG: {'ON (' + str(blur_strength) + ')' if enable_blur else 'OFF'}")
        window.write_event_value("-LOG_LINE-", f"[CONFIG] Expected total duration: {format_duration(total_duration)} ({total_duration:.2f}s)")

        # Build filtergraph script
        script_content = build_filter_complex_script(
            items=items,
            target_w=target_w,
            target_h=target_h,
            target_fps=target_fps,
            enable_blur=enable_blur,
            blur_strength=blur_strength
        )

        with open(filter_script_path, "w", encoding="utf-8") as f:
            f.write(script_content)

        # Assemble FFmpeg command
        cmd: List[str] = [FFMPEG, "-y", "-nostdin"]

        # Add all input files
        for it in items:
            cmd.extend(["-i", it["path"]])

        # Use filter_complex_script to avoid Windows command line character limits
        cmd.extend([
            "-filter_complex_script", filter_script_path,
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            output_path
        ])

        window.write_event_value("-LOG_LINE-", f"[EXEC] Launching FFmpeg to render: {output_path}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        with process_lock:
            current_process = proc

        stderr_lines: List[str] = []

        def capture_stderr(pipe):
            try:
                for line in iter(pipe.readline, ''):
                    if line:
                        s = line.rstrip()
                        stderr_lines.append(s)
                        if len(stderr_lines) > 50:
                            stderr_lines.pop(0)
                pipe.close()
            except Exception:
                pass

        t_err = threading.Thread(target=capture_stderr, args=(proc.stderr,), daemon=True)
        t_err.start()

        # Parse -progress output from stdout
        time_regex = re.compile(r"out_time_us=(\d+)")
        time_ms_regex = re.compile(r"out_time_ms=(\d+)")
        out_time_regex = re.compile(r"out_time=([\d:.]+)")

        last_progress_update = 0.0

        for line in iter(proc.stdout.readline, ''):
            if not line or cancel_requested:
                break
            line_str = line.strip()

            cur_sec = -1.0
            m_us = time_regex.match(line_str)
            if m_us:
                cur_sec = float(m_us.group(1)) / 1_000_000.0
            else:
                m_ms = time_ms_regex.match(line_str)
                if m_ms:
                    cur_sec = float(m_ms.group(1)) / 1_000.0
                else:
                    m_time = out_time_regex.match(line_str)
                    if m_time:
                        cur_sec = parse_time_to_seconds(m_time.group(1))

            if cur_sec >= 0 and total_duration > 0:
                now = time.time()
                if now - last_progress_update >= 0.2:
                    last_progress_update = now
                    fraction = min(1.0, max(0.0, cur_sec / total_duration))
                    elapsed = now - start_time
                    if fraction > 0.01:
                        total_est = elapsed / fraction
                        eta = max(0, total_est - elapsed)
                        eta_str = format_duration(eta)
                    else:
                        eta_str = "--:--"

                    pct = int(fraction * 100)
                    window.write_event_value("-PROGRESS_UPDATE-", {
                        "percent": pct,
                        "elapsed": format_duration(elapsed),
                        "eta": eta_str,
                        "current_sec": cur_sec
                    })

        proc.stdout.close()
        proc.wait()
        t_err.join()

        with process_lock:
            current_process = None

        if cancel_requested:
            window.write_event_value("-LOG_LINE-", "[STOP] Process was stopped by user.")
            # Remove partial output
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
                    window.write_event_value("-LOG_LINE-", "[CLEANUP] Deleted partial output file.")
            except Exception as ex:
                window.write_event_value("-LOG_LINE-", f"[WARN] Could not delete partial output: {ex}")
            window.write_event_value("-MERGE_DONE-", {"status": "Stopped"})
            return

        if proc.returncode == 0 and os.path.isfile(output_path):
            total_elapsed = time.time() - start_time
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            window.write_event_value("-LOG_LINE-", f"[SUCCESS] Merge completed in {format_duration(total_elapsed)} ({file_size_mb:.2f} MB).")
            window.write_event_value("-LOG_LINE-", f"[OUTPUT] Saved to: {output_path}")
            window.write_event_value("-MERGE_DONE-", {"status": "Success", "output": output_path})
        else:
            window.write_event_value("-LOG_LINE-", f"[ERROR] FFmpeg failed with exit code {proc.returncode}.")
            if stderr_lines:
                window.write_event_value("-LOG_LINE-", "--- Last lines of FFmpeg stderr ---")
                for err_line in stderr_lines[-15:]:
                    window.write_event_value("-LOG_LINE-", f"  {err_line}")
                window.write_event_value("-LOG_LINE-", "-----------------------------------")
            # Remove partial output on failure
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception:
                pass
            window.write_event_value("-MERGE_DONE-", {"status": "Failed"})

    except Exception as e:
        window.write_event_value("-LOG_LINE-", f"[FATAL] Exception in merge worker: {e}")
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except Exception:
            pass
        window.write_event_value("-MERGE_DONE-", {"status": "Error", "message": str(e)})

    finally:
        # Clean up temporary filter script
        if filter_script_path and os.path.exists(filter_script_path):
            try:
                os.remove(filter_script_path)
            except Exception:
                pass


# ---------------- GUI Theme & Styling ----------------
sg.theme("DarkGrey14")

FONT_FAMILY = "Segoe UI"
FONT_TITLE = (FONT_FAMILY, 15, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 9)
FONT_LABEL = (FONT_FAMILY, 9, "bold")
FONT_TEXT = (FONT_FAMILY, 9)
FONT_MONO = ("Consolas", 9)
FONT_BTN = (FONT_FAMILY, 9, "bold")


def build_gui_layout() -> List[Any]:
    # --- Input Files Frame ---
    input_btns_col = [
        [sg.Button("Add Files", key="-ADD_FILES-", button_color=("white", "#30363d"), font=FONT_BTN, size=(12, 1))],
        [sg.Button("Add Folder", key="-ADD_FOLDER-", button_color=("white", "#30363d"), font=FONT_TEXT, size=(12, 1))],
        [sg.Button("Remove Selected", key="-REMOVE_SEL-", button_color=("white", "#30363d"), font=FONT_TEXT, size=(12, 1))],
        [sg.Button("Clear All", key="-CLEAR-", button_color=("white", "#da3633"), font=FONT_TEXT, size=(12, 1))],
        [sg.HorizontalSeparator(pad=((0, 0), (6, 6)))],
        [sg.Button("Move Up ▲", key="-MOVE_UP-", button_color=("white", "#30363d"), font=FONT_TEXT, size=(12, 1))],
        [sg.Button("Move Down ▼", key="-MOVE_DOWN-", button_color=("white", "#30363d"), font=FONT_TEXT, size=(12, 1))],
        [sg.Button("Sort by Name", key="-SORT_NAME-", button_color=("white", "#30363d"), font=FONT_TEXT, size=(12, 1))],
    ]

    input_table_col = [
        [sg.Table(
            values=[],
            headings=["#", "Filename", "Duration", "Resolution", "Audio", "Full Path"],
            key="-TABLE_INPUTS-",
            col_widths=[4, 30, 10, 12, 8, 35],
            auto_size_columns=False,
            justification="left",
            font=FONT_TEXT,
            header_font=FONT_LABEL,
            expand_x=True,
            expand_y=True,
            num_rows=9,
            enable_events=True,
            select_mode=sg.TABLE_SELECT_MODE_EXTENDED,
            background_color="#0d1117",
            text_color="#c9d1d9",
            alternating_row_color="#161b22",
        )],
        [
            sg.Text("Clips: 0", key="-TXT_COUNT-", font=FONT_LABEL, text_color="#58a6ff"),
            sg.Text(" | ", font=FONT_LABEL),
            sg.Text("Total Duration: 00:00", key="-TXT_TOTAL_DUR-", font=FONT_LABEL, text_color="#7ee787"),
            sg.Push(),
            sg.Text("Order top-to-bottom represents final merge sequence.", font=FONT_SUBTITLE, text_color="#8b949e")
        ]
    ]

    inputs_frame = [
        [
            sg.Column(input_table_col, expand_x=True, expand_y=True),
            sg.Column(input_btns_col, vertical_alignment="top", pad=((6, 0), (0, 0)))
        ]
    ]

    # --- Output Settings Frame ---
    output_settings_frame = [
        [
            sg.Text("Output File:", size=(11, 1), font=FONT_LABEL),
            sg.InputText(key="-OUT_PATH-", expand_x=True, font=FONT_TEXT),
            sg.FileSaveAs("Browse", target="-OUT_PATH-", file_types=(("MP4 Video", "*.mp4"), ("All Files", "*.*")),
                          button_color=("white", "#30363d"), font=FONT_TEXT, size=(8, 1))
        ],
        [
            sg.Text("Resolution:", size=(11, 1), font=FONT_LABEL),
            sg.Combo(
                ["Same as first video", "1920x1080", "1280x720", "1080x1920 (vertical)", "1080x1080"],
                default_value="Same as first video",
                key="-RES_PRESET-",
                readonly=True,
                size=(22, 1),
                font=FONT_TEXT
            ),
            sg.Text("FPS:", font=FONT_LABEL, pad=((15, 4), (0, 0))),
            sg.Combo(
                ["Same as first video", "24", "25", "30", "60"],
                default_value="Same as first video",
                key="-FPS_PRESET-",
                readonly=True,
                size=(20, 1),
                font=FONT_TEXT
            ),
        ],
        [
            sg.Text("Quality (CRF):", size=(11, 1), font=FONT_LABEL),
            sg.Slider(
                range=(14, 30),
                default_value=20,
                orientation="h",
                size=(18, 15),
                key="-CRF-",
                font=FONT_TEXT,
                enable_events=False
            ),
            sg.Text("Speed Preset:", font=FONT_LABEL, pad=((15, 4), (0, 0))),
            sg.Combo(
                ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"],
                default_value="veryfast",
                key="-PRESET-",
                readonly=True,
                size=(12, 1),
                font=FONT_TEXT
            ),
            sg.Text("(lower CRF = higher quality)", font=FONT_SUBTITLE, text_color="#8b949e", pad=((8, 0), (0, 0)))
        ]
    ]

    # --- Blur Background Frame ---
    blur_frame = [
        [
            sg.Checkbox(
                "Enable blurred background for clips not matching output aspect ratio",
                key="-ENABLE_BLUR-",
                default=False,
                enable_events=True,
                font=FONT_LABEL
            ),
            sg.Push(),
            sg.Text("Blur Strength:", font=FONT_LABEL, pad=((10, 4), (0, 0))),
            sg.Slider(
                range=(10, 60),
                default_value=30,
                orientation="h",
                size=(16, 15),
                key="-BLUR_STRENGTH-",
                font=FONT_TEXT,
                disabled=True
            ),
            sg.Text("(Default: 30)", font=FONT_SUBTITLE, text_color="#8b949e")
        ],
        [
            sg.Text(
                "When off: clips are padded with black bars. When on: clips with mismatched aspect ratios get a smooth blurred backdrop.",
                font=FONT_SUBTITLE,
                text_color="#8b949e"
            )
        ]
    ]

    # --- Actions, Progress & Status Row ---
    actions_row = [
        sg.Button("  Merge Videos  ", key="-RUN_MERGE-", button_color=("white", "#238636"), font=FONT_BTN, size=(16, 1), pad=((0, 6), (0, 0))),
        sg.Button("  Stop  ", key="-STOP-", disabled=True, button_color=("white", "#da3633"), font=FONT_BTN, size=(10, 1), pad=((0, 6), (0, 0))),
        sg.Button("Clear Log", key="-CLR_LOG-", button_color=("white", "#30363d"), font=FONT_TEXT, size=(10, 1)),
        sg.Push(),
        sg.Text("Status:", font=FONT_LABEL),
        sg.Text("Idle", key="-STATUS-", font=FONT_BTN, text_color="#58a6ff", size=(14, 1))
    ]

    progress_row = [
        sg.ProgressBar(max_value=100, orientation='h', size=(35, 18), key="-PROGRESS_BAR-", expand_x=True, bar_color=("#238636", "#21262d")),
        sg.Text("0%", key="-TXT_PCT-", font=FONT_LABEL, size=(5, 1)),
        sg.Text("Elapsed: 00:00", key="-TXT_ELAPSED-", font=FONT_TEXT, size=(14, 1)),
        sg.Text("ETA: --:--", key="-TXT_ETA-", font=FONT_TEXT, size=(12, 1)),
    ]

    # --- Live Log Frame ---
    log_frame = [
        [
            sg.Multiline(
                size=(90, 9),
                key="-LOG-",
                autoscroll=True,
                disabled=True,
                font=FONT_MONO,
                background_color="#0d1117",
                text_color="#c9d1d9",
                expand_x=True,
                expand_y=True
            )
        ]
    ]

    # Assemble main layout
    layout = [
        [
            sg.Column([
                [
                    sg.Text(APP_NAME, font=FONT_TITLE, text_color="#58a6ff"),
                    sg.Text(f"— {AUTHOR}", font=FONT_SUBTITLE, text_color="#8b949e", pad=((6, 0), (5, 0)))
                ],
                [
                    sg.Text("Merge and normalize scene clips into a single video with optional blurred background",
                            font=FONT_SUBTITLE, text_color="#8b949e", pad=((0, 0), (0, 4)))
                ]
            ], pad=((0, 0), (0, 4)))
        ],
        [sg.Frame(" Video Clips to Merge ", inputs_frame, font=FONT_LABEL, expand_x=True, expand_y=True, pad=((0, 0), (0, 4)))],
        [sg.Frame(" Output Settings ", output_settings_frame, font=FONT_LABEL, expand_x=True, pad=((0, 0), (0, 4)))],
        [sg.Frame(" Blur Background Option ", blur_frame, font=FONT_LABEL, expand_x=True, pad=((0, 0), (0, 4)))],
        actions_row,
        progress_row,
        [sg.Frame(" Live Process Log ", log_frame, font=FONT_LABEL, expand_x=True, expand_y=True, pad=((0, 0), (4, 0)))]
    ]

    return layout


# ---------------- Main Controller ----------------

def main():
    global FFMPEG, FFPROBE, cancel_requested, merge_worker_thread

    layout = build_gui_layout()
    icon_file = resource_path("icon.ico")
    app_icon = icon_file if os.path.isfile(icon_file) else None

    window = sg.Window(
        APP_TITLE,
        layout,
        resizable=True,
        finalize=True,
        icon=app_icon,
        margins=(15, 12),
        element_padding=(4, 4),
        size=(940, 780)
    )

    # Startup checks for ffmpeg / ffprobe
    if not FFMPEG:
        window["-LOG-"].print("[WARN] ffmpeg.exe not found automatically.")
        FFMPEG = prompt_for_binary("ffmpeg")
    if not FFPROBE:
        window["-LOG-"].print("[WARN] ffprobe.exe not found automatically.")
        FFPROBE = prompt_for_binary("ffprobe")

    if not FFMPEG or not FFPROBE:
        missing = []
        if not FFMPEG:
            missing.append("ffmpeg.exe")
        if not FFPROBE:
            missing.append("ffprobe.exe")
        err_msg = (
            f"Missing required component(s): {', '.join(missing)}\n\n"
            "FFmpeg/FFprobe were not found or selected.\n"
            "You can still browse for them when starting a job."
        )
        sg.popup_error(err_msg, title="FFmpeg Not Found")
        window["-LOG-"].print(f"[ERROR] {err_msg}")
    else:
        window["-LOG-"].print(f"[INIT] ffmpeg: {FFMPEG}")
        window["-LOG-"].print(f"[INIT] ffprobe: {FFPROBE}")

    # Internal clip collection: list of dicts {path, meta: {duration, width, height, fps, has_audio}}
    input_items: List[Dict[str, Any]] = []

    # Session remembered folders
    last_open_folder = os.path.expanduser("~")
    last_output_dir = os.path.expanduser("~")
    user_edited_output = False

    def update_table_view() -> None:
        nonlocal input_items
        table_rows = []
        total_sec = 0.0
        for idx, item in enumerate(input_items, 1):
            m = item["meta"]
            dur_str = format_duration(m.get("duration", 0.0))
            total_sec += m.get("duration", 0.0)
            w = m.get("width", 0)
            h = m.get("height", 0)
            res_str = f"{w}x{h}" if w and h else "Unknown"
            audio_str = "Yes" if m.get("has_audio") else "No (Silent)"
            table_rows.append([
                str(idx),
                os.path.basename(item["path"]),
                dur_str,
                res_str,
                audio_str,
                item["path"]
            ])

        window["-TABLE_INPUTS-"].update(values=table_rows)
        window["-TXT_COUNT-"].update(f"Clips: {len(input_items)}")
        window["-TXT_TOTAL_DUR-"].update(f"Total Duration: {format_duration(total_sec)} ({total_sec:.1f}s)")

        # Auto-propose default output name if user hasn't explicitly customized it
        if input_items and not user_edited_output:
            first_dir = os.path.dirname(input_items[0]["path"])
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_out = os.path.join(first_dir, f"merged_{timestamp}.mp4")
            window["-OUT_PATH-"].update(default_out)

    def set_controls_running(running: bool, status_text: str = "Idle") -> None:
        window["-RUN_MERGE-"].update(disabled=running)
        window["-ADD_FILES-"].update(disabled=running)
        window["-ADD_FOLDER-"].update(disabled=running)
        window["-REMOVE_SEL-"].update(disabled=running)
        window["-CLEAR-"].update(disabled=running)
        window["-MOVE_UP-"].update(disabled=running)
        window["-MOVE_DOWN-"].update(disabled=running)
        window["-SORT_NAME-"].update(disabled=running)
        window["-RES_PRESET-"].update(disabled=running)
        window["-FPS_PRESET-"].update(disabled=running)
        window["-CRF-"].update(disabled=running)
        window["-PRESET-"].update(disabled=running)
        window["-ENABLE_BLUR-"].update(disabled=running)
        if not running and values.get("-ENABLE_BLUR-"):
            window["-BLUR_STRENGTH-"].update(disabled=False)
        else:
            window["-BLUR_STRENGTH-"].update(disabled=True)

        window["-STOP-"].update(disabled=not running)
        window["-STATUS-"].update(status_text)

    # ---------------- Main Event Loop ----------------
    while True:
        event, values = window.read(timeout=100)
        if event == sg.WIN_CLOSED:
            cancel_requested = True
            with process_lock:
                if current_process and current_process.poll() is None:
                    try:
                        terminate_process_tree(current_process)
                    except Exception:
                        pass
            break

        # Log clearing
        if event == "-CLR_LOG-":
            window["-LOG-"].update("")

        # Streamed log line from worker
        if event == "-LOG_LINE-":
            msg = values.get("-LOG_LINE-")
            if msg is not None:
                window["-LOG-"].print(msg)

        # Blur checkbox toggling
        if event == "-ENABLE_BLUR-":
            is_enabled = bool(values.get("-ENABLE_BLUR-", False))
            window["-BLUR_STRENGTH-"].update(disabled=not is_enabled)

        # Add Files
        if event == "-ADD_FILES-":
            chosen = sg.popup_get_file(
                "Select Video Clips",
                multiple_files=True,
                initial_folder=last_open_folder,
                file_types=(("Video Files", "*.mp4;*.mkv;*.mov;*.ts;*.m4v;*.avi;*.webm;*.flv;*.wmv"), ("All Files", "*.*")),
                title="Add Video Clips"
            )
            if chosen:
                raw_paths = chosen.split(";") if ";" in chosen else [chosen]
                added_count = 0
                for p in raw_paths:
                    p = p.strip()
                    if p and os.path.isfile(p):
                        last_open_folder = os.path.dirname(p)
                        if not any(it["path"] == p for it in input_items):
                            window["-LOG-"].print(f"[PROBE] Analyzing: {os.path.basename(p)}...")
                            meta = probe_video_metadata(p)
                            input_items.append({"path": p, "meta": meta})
                            added_count += 1
                if added_count > 0:
                    update_table_view()
                    window["-LOG-"].print(f"[INFO] Added {added_count} video file(s).")

        # Add Folder
        if event == "-ADD_FOLDER-":
            folder = sg.popup_get_folder(
                "Select folder containing video clips",
                initial_folder=last_open_folder,
                title="Add Videos from Folder"
            )
            if folder and os.path.isdir(folder):
                last_open_folder = folder
                added_count = 0
                for root, _, files in os.walk(folder):
                    for f in sorted(files):
                        ext = os.path.splitext(f)[1].lower()
                        if ext in VIDEO_EXTS:
                            p = os.path.join(root, f)
                            if not any(it["path"] == p for it in input_items):
                                meta = probe_video_metadata(p)
                                input_items.append({"path": p, "meta": meta})
                                added_count += 1
                if added_count > 0:
                    update_table_view()
                    window["-LOG-"].print(f"[INFO] Added {added_count} video file(s) from {folder}.")
                else:
                    window["-LOG-"].print(f"[WARN] No new video files found in {folder}.")

        # Remove Selected
        if event == "-REMOVE_SEL-":
            selected_indices = values.get("-TABLE_INPUTS-", [])
            if selected_indices:
                # Remove in descending order so indices remain valid
                for idx in sorted(selected_indices, reverse=True):
                    if 0 <= idx < len(input_items):
                        del input_items[idx]
                update_table_view()

        # Clear All
        if event == "-CLEAR-":
            if input_items:
                input_items.clear()
                user_edited_output = False
                update_table_view()
                window["-OUT_PATH-"].update("")
                window["-PROGRESS_BAR-"].update(0)
                window["-TXT_PCT-"].update("0%")
                window["-TXT_ELAPSED-"].update("Elapsed: 00:00")
                window["-TXT_ETA-"].update("ETA: --:--")
                window["-LOG-"].print("[INFO] Cleared all input clips.")

        # Move Up
        if event == "-MOVE_UP-":
            selected_indices = values.get("-TABLE_INPUTS-", [])
            if selected_indices and len(selected_indices) == 1:
                idx = selected_indices[0]
                if idx > 0:
                    input_items[idx - 1], input_items[idx] = input_items[idx], input_items[idx - 1]
                    update_table_view()
                    window["-TABLE_INPUTS-"].update(select_rows=[idx - 1])

        # Move Down
        if event == "-MOVE_DOWN-":
            selected_indices = values.get("-TABLE_INPUTS-", [])
            if selected_indices and len(selected_indices) == 1:
                idx = selected_indices[0]
                if idx < len(input_items) - 1:
                    input_items[idx + 1], input_items[idx] = input_items[idx], input_items[idx + 1]
                    update_table_view()
                    window["-TABLE_INPUTS-"].update(select_rows=[idx + 1])

        # Sort by Name
        if event == "-SORT_NAME-":
            if len(input_items) > 1:
                input_items.sort(key=lambda it: os.path.basename(it["path"]).lower())
                update_table_view()
                window["-LOG-"].print("[INFO] Sorted clips by filename.")

        # Detect manual edit of output path
        if event == "-OUT_PATH-":
            user_edited_output = True

        # Stop Button
        if event == "-STOP-":
            cancel_requested = True
            with process_lock:
                if current_process and current_process.poll() is None:
                    try:
                        terminate_process_tree(current_process)
                        window["-LOG-"].print("[STOP] Terminating FFmpeg process...")
                    except Exception as ex:
                        window["-LOG-"].print(f"[STOP] Error terminating process: {ex}")
            window["-STOP-"].update(disabled=True)
            window["-STATUS-"].update("Stopping...")

        # Progress Update from Worker
        if event == "-PROGRESS_UPDATE-":
            data = values["-PROGRESS_UPDATE-"]
            pct = data.get("percent", 0)
            window["-PROGRESS_BAR-"].update(pct)
            window["-TXT_PCT-"].update(f"{pct}%")
            window["-TXT_ELAPSED-"].update(f"Elapsed: {data.get('elapsed', '00:00')}")
            window["-TXT_ETA-"].update(f"ETA: {data.get('eta', '--:--')}")

        # Merge Done Event
        if event == "-MERGE_DONE-":
            res = values["-MERGE_DONE-"]
            st = res.get("status", "Done")
            set_controls_running(False, st)
            if st == "Success":
                window["-PROGRESS_BAR-"].update(100)
                window["-TXT_PCT-"].update("100%")
                window["-TXT_ETA-"].update("ETA: 00:00")
                sg.popup("Videos merged successfully!", title="Success")
            elif st == "Stopped":
                window["-PROGRESS_BAR-"].update(0)
                window["-TXT_PCT-"].update("0%")
                sg.popup("Process stopped by user. Partial output was cleaned up.", title="Stopped")
            else:
                sg.popup_error(f"Merge operation failed. Check log for details.", title="Merge Error")

        # Start Merge Execution
        if event == "-RUN_MERGE-":
            if not input_items:
                sg.popup("Please add at least one video clip to merge.", title="No Inputs")
                continue

            # Ensure binaries
            if not FFMPEG:
                FFMPEG = prompt_for_binary("ffmpeg")
                if not FFMPEG:
                    sg.popup_error("FFmpeg executable is required.", title="Missing FFmpeg")
                    continue
            if not FFPROBE:
                FFPROBE = prompt_for_binary("ffprobe")
                if not FFPROBE:
                    sg.popup_error("FFprobe executable is required.", title="Missing FFprobe")
                    continue

            out_path = values.get("-OUT_PATH-", "").strip()
            if not out_path:
                sg.popup("Please select an output file path.", title="Output Required")
                continue

            # Ensure valid directory and extension
            out_dir = os.path.dirname(out_path)
            if not out_dir:
                out_dir = os.path.dirname(input_items[0]["path"])
                out_path = os.path.join(out_dir, out_path)
                window["-OUT_PATH-"].update(out_path)

            if not os.path.splitext(out_path)[1]:
                out_path = out_path + ".mp4"
                window["-OUT_PATH-"].update(out_path)

            try:
                os.makedirs(out_dir, exist_ok=True)
                # Test write permission
                test_tmp = os.path.join(out_dir, f".test_write_{os.getpid()}.tmp")
                with open(test_tmp, "w") as f:
                    f.write("test")
                os.remove(test_tmp)
            except Exception as ex:
                sg.popup_error(f"Cannot write to output folder:\n{out_dir}\nError: {ex}", title="Permission Error")
                continue

            # Overwrite protection check
            if os.path.exists(out_path):
                confirm = sg.popup_yes_no(
                    f"The file:\n{os.path.basename(out_path)}\nalready exists in:\n{out_dir}\n\nDo you want to overwrite it?",
                    title="Confirm Overwrite"
                )
                if confirm != "Yes":
                    continue

            # Determine Target Resolution
            res_choice = values.get("-RES_PRESET-", "Same as first video")
            first_meta = input_items[0]["meta"]
            first_w = first_meta.get("width", 0) or 1920
            first_h = first_meta.get("height", 0) or 1080

            if res_choice == "1920x1080":
                target_w, target_h = 1920, 1080
            elif res_choice == "1280x720":
                target_w, target_h = 1280, 720
            elif res_choice == "1080x1920 (vertical)":
                target_w, target_h = 1080, 1920
            elif res_choice == "1080x1080":
                target_w, target_h = 1080, 1080
            else:
                target_w, target_h = first_w, first_h

            # Determine Target FPS
            fps_choice = values.get("-FPS_PRESET-", "Same as first video")
            first_fps = first_meta.get("fps", 0) or 25.0
            if fps_choice in ("24", "25", "30", "60"):
                try:
                    target_fps = float(fps_choice)
                except ValueError:
                    target_fps = first_fps
            else:
                target_fps = first_fps if first_fps > 0 else 25.0

            crf = int(values.get("-CRF-", 20))
            preset = values.get("-PRESET-", "veryfast")
            enable_blur = bool(values.get("-ENABLE_BLUR-", False))
            blur_strength = int(values.get("-BLUR_STRENGTH-", 30))

            total_duration = sum(it["meta"].get("duration", 0.0) for it in input_items)

            # Reset progress UI
            window["-PROGRESS_BAR-"].update(0)
            window["-TXT_PCT-"].update("0%")
            window["-TXT_ELAPSED-"].update("Elapsed: 00:00")
            window["-TXT_ETA-"].update("ETA: Calculating...")

            set_controls_running(True, "Merging...")

            # Launch worker thread
            merge_worker_thread = threading.Thread(
                target=merge_worker,
                args=(
                    list(input_items),
                    out_path,
                    target_w,
                    target_h,
                    target_fps,
                    crf,
                    preset,
                    enable_blur,
                    blur_strength,
                    total_duration,
                    window
                ),
                daemon=True
            )
            merge_worker_thread.start()

    window.close()


if __name__ == "__main__":
    main()
