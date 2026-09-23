import os
import time
import webbrowser
import pyautogui
import pygetwindow as gw
import keyboard
import pyperclip
import subprocess
import ctypes
from datetime import datetime
import re

def open_chrome_new_tab(url="https://www.google.com"):
    try:
        # Explicitly launch Chrome via Windows start command
        subprocess.Popen(f'start chrome "{url}"', shell=True)
        return f"Opened new Chrome tab."
    except Exception as e:
        return f"Failed to open Chrome: {e}"

def take_screenshot():
    try:
        # Fix DPI scaling issue on Windows for correct screenshot dimensions
        if os.name == 'nt':
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except AttributeError:
                pass

        # Create screenshots directory relative to this script
        base_dir = os.path.dirname(os.path.abspath(__file__))
        screenshots_dir = os.path.join(base_dir, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(screenshots_dir, f"screenshot_{timestamp}.png")
        
        screenshot = pyautogui.screenshot(allScreens=True)
        screenshot.save(filename)
        return f"Screenshot saved."
    except Exception as e:
        return f"Failed to take screenshot: {e}"

def open_application(app_name):
    # Mapping common app names to executable commands
    app_commands = {
        "calculator": "calc.exe",
        "notepad": "notepad.exe",
        "paint": "mspaint.exe",
        "ms paint": "mspaint.exe",
        "explorer": "explorer.exe",
        "cmd": "cmd.exe",
        "command prompt": "cmd.exe",
        "word": "winword.exe",
        "ms word": "winword.exe",
        "excel": "excel.exe",
        "ms excel": "excel.exe",
        "powerpoint": "powerpnt.exe",
        "ms powerpoint": "powerpnt.exe"
    }
    
    cmd = app_commands.get(app_name.lower(), app_name)
    try:
        # Use Windows 'start' to explicitly launch in foreground/windowed mode
        subprocess.Popen(f'start "" {cmd}', shell=True)
        return f"Opened {app_name}."
    except Exception as e:
        return f"Failed to open {app_name}: {e}"

def close_window_by_name(target=""):
    try:
        target = target.strip().lower() if target else ""
        
        # Clean up common conversational words
        if target in ["this window", "the window", "active window", "current window", "this", "it"]:
            target = ""
            
        if not target:
            # Close active window if no specific target
            active_window = gw.getActiveWindow()
            if active_window:
                title = active_window.title
                active_window.close()
                return f"Closed {title}."
            else:
                pyautogui.hotkey('alt', 'f4')
                return "Sent close window command."
        else:
            # Try to find window by title substring
            all_windows = gw.getAllWindows()
            for w in all_windows:
                if w.title and target in w.title.lower():
                    w.close()
                    return f"Closed {w.title}."
            return f"Could not find a window matching '{target}' to close."
    except Exception as e:
        return f"Failed to close window: {e}"

def switch_tab(direction="next"):
    try:
        if direction.lower() in ["next", "right"]:
            pyautogui.hotkey('ctrl', 'tab')
            return "Switched to the next tab."
        elif direction.lower() in ["previous", "left"]:
            pyautogui.hotkey('ctrl', 'shift', 'tab')
            return "Switched to the previous tab."
        else:
            return "Unknown direction for tab switch."
    except Exception as e:
        return f"Failed to switch tab: {e}"

def lock_screen():
    try:
        # Windows API call to lock workstation
        ctypes.windll.user32.LockWorkStation()
        return "Screen locked."
    except Exception as e:
        return f"Failed to lock screen: {e}"

def mute_volume():
    try:
        # Send volume mute media key
        pyautogui.press("volumemute")
        return "Muted the volume."
    except Exception as e:
        return f"Failed to mute volume: {e}"

def set_volume(level="up"):
    try:
        if level.lower() == "up":
            # Press multiple times to make a noticeable difference
            for _ in range(5):
                pyautogui.press("volumeup")
            return "Volume increased."
        elif level.lower() == "down":
            for _ in range(5):
                pyautogui.press("volumedown")
            return "Volume decreased."
        else:
            return "Unknown volume command."
    except Exception as e:
        return f"Failed to change volume: {e}"

def open_file_explorer():
    try:
        # os.startfile reliably forces the Explorer GUI to open in the foreground on Windows
        os.startfile(os.path.expanduser("~"))
        return "Opened File Explorer."
    except Exception as e:
        return f"Failed to open File Explorer: {e}"

def open_dynamic_website(target):
    try:
        # Strip spaces and construct a standard URL
        domain = target.replace(" ", "")
        url = f"https://www.{domain}.com"
        subprocess.Popen(f'start chrome "{url}"', shell=True)
        return f"Opened {target} in Chrome."
    except Exception as e:
        return f"Failed to open {target}: {e}"

# ─── COMMAND MATCHING LAYER ──────────────────────────────────

# List of (regex_pattern, handler_function)
AUTOMATION_PATTERNS = [
    (r"take (?:a )?screenshot", lambda m: take_screenshot()),
    (r"open (?:a )?new tab", lambda m: open_chrome_new_tab()),
    (r"open (?:the )?(calculator|notepad|paint|ms paint|cmd|command prompt|word|ms word|excel|ms excel|powerpoint|ms powerpoint)", lambda m: open_application(m.group(1))),
    (r"open (?:the )?(?:file )?explorer", lambda m: open_file_explorer()),
    (r"^close(?: (.*))?$", lambda m: close_window_by_name(m.group(1))),
    (r"switch (?:to )?(?:the )?(next|previous|right|left) tab", lambda m: switch_tab(m.group(1))),
    (r"lock (?:the )?(?:screen|computer|workstation)", lambda m: lock_screen()),
    (r"mute (?:the )?volume|mute", lambda m: mute_volume()),
    (r"(?:turn|volume) (up|down)", lambda m: set_volume(m.group(1))),
    # Fallback for dynamic websites (e.g. "open flipkart")
    (r"open (?:the )?(.+)", lambda m: open_dynamic_website(m.group(1)))
]

def match_automation_command(user_input):
    """
    Checks if the user_input matches any automation command.
    Returns the spoken-friendly string result if matched, else None.
    """
    user_input = user_input.lower().strip()
    
    for pattern, handler in AUTOMATION_PATTERNS:
        match = re.search(pattern, user_input)
        if match:
            try:
                return handler(match)
            except Exception as e:
                return f"Error executing command: {e}"
    
    return None

if __name__ == "__main__":
    # Simple test REPL
    print("Automation Matcher Test (type 'exit' to quit)")
    while True:
        cmd = input("> ")
        if cmd.lower() == 'exit':
            break
        result = match_automation_command(cmd)
        if result:
            print(f"Matched! Result: {result}")
        else:
            print("No match.")
