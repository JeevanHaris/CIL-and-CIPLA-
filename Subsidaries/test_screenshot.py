import traceback
try:
    import pyautogui
    pyautogui.screenshot('test_pyautogui.png')
    print("pyautogui success")
except Exception as e:
    print("pyautogui error:", e)

try:
    from PIL import ImageGrab
    ImageGrab.grab(all_screens=True).save('test_imagegrab.png')
    print("ImageGrab success")
except Exception as e:
    print("ImageGrab error:", e)

try:
    import mss
    with mss.mss() as sct:
        sct.shot(output='test_mss.png')
    print("mss success")
except Exception as e:
    print("mss error:", e)
