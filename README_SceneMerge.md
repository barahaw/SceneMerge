# SceneMerge

**Author:** Baraa Hawamdeh  
**Purpose:** Standalone desktop GUI tool designed to merge video clips (such as the "removed parts" videos produced by SceneTrim named `<name>_removed.<ext>`) into a single normalized video file.

SceneMerge normalizes clips of varying resolutions, aspect ratios, framerates, and audio codecs in a single FFmpeg pass using `filter_complex_script`, with an optional blurred background effect for aspect ratio mismatches.

---

## Key Features

1. **Clip Management & Inspection**:
   - Multi-file selection (`Add Files`) and recursive folder addition (`Add Folder`).
   - Remove clips, clear queue, reorder clips (`Move Up ▲`, `Move Down ▼`), and sort alphabetically (`Sort by Name`).
   - Inspects each clip via `ffprobe` to display duration, resolution, audio presence, and full path.
   - Live total duration calculation for the merged output.

2. **Output Customization**:
   - Default output path formatted as `merged_<timestamp>.mp4` in the folder of the first input video.
   - Resolution presets: *Same as first video*, *1920x1080*, *1280x720*, *1080x1920 (vertical)*, *1080x1080*.
   - Framerate presets: *Same as first video*, *24*, *25*, *30*, *60*.
   - Quality control with CRF slider (default 20) and FFmpeg presets (`veryfast`, `faster`, `fast`, `medium`, `slow`, etc.).

3. **Smart Blurred Background**:
   - **Off**: Clips are scaled to fit with clean black padding.
   - **On**: Clips that do not match the target aspect ratio are automatically placed over a blurred, zoomed background of themselves.
   - Dynamic blur strength slider (10 to 60).
   - Skips blurring when a clip's aspect ratio already matches the target resolution, maximizing render speed.

4. **Robust Audio & Video Normalization**:
   - Standardizes audio across all clips to 48000 Hz stereo AAC.
   - Generates silent audio streams (`anullsrc`) for clips without audio tracks, preventing concat filter failures.
   - Color space normalization (`yuv420p`, `setsar=1`) and Web optimization (`-movflags +faststart`).
   - Generates and passes a temporary filtergraph script via `-filter_complex_script` to prevent command-line length limits on Windows and reliably handle non-ASCII/Arabic filenames and spaces.

5. **Responsiveness & Safety**:
   - Background worker thread keeps the GUI completely smooth.
   - Live progress bar with elapsed time and ETA calculations.
   - Immediate **Stop** button that kills the active FFmpeg process tree and cleans up partial output files.
   - Overwrite protection prompt when the output file already exists.
   - Automatic fallback discovery for FFmpeg and FFprobe binaries.

---

## Requirements

- Python 3.9+
- [FreeSimpleGUI](https://pypi.org/project/FreeSimpleGUI/)
- FFmpeg and FFprobe installed on the system PATH or placed alongside the script/executable.

### Installing Dependencies

```bash
pip install FreeSimpleGUI
```

Ensure FFmpeg and FFprobe are accessible. On Windows with winget:
```bash
winget install Gyan.FFmpeg
```

---

## How to Run

Run SceneMerge directly with Python:

```bash
python SceneMerge.py
```

---

## How to Build Standalone Executable (.exe)

You can build a single, portable `.exe` file without external Python runtime dependencies using PyInstaller:

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```

2. Package SceneMerge:
   ```bash
   pyinstaller --noconsole --onefile --name SceneMerge SceneMerge.py
   ```

3. Find your standalone executable in the `dist/` directory:
   ```bash
   dist/SceneMerge.exe
   ```

*Note: For runtime portability on machines without FFmpeg installed globally, place `ffmpeg.exe` and `ffprobe.exe` in the same directory as `SceneMerge.exe` or within a `bin/` subfolder next to it.*
