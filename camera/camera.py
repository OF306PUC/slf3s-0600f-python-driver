import cv2
import os
import time
from datetime import datetime

SAVE_DIR = "photos"
INTERVAL_SECONDS = 60*2
CAMERA_INDEX = 0
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera index {CAMERA_INDEX}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print(f"Requested {FRAME_WIDTH}x{FRAME_HEIGHT}, camera gave {actual_w:.0f}x{actual_h:.0f}")

    print(f"Camera open. Saving photo to '{SAVE_DIR}/' every {INTERVAL_SECONDS}s. Ctrl+C to stop.")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Frame grab failed, retry next cycle.")
            else:
                filename = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
                path = os.path.join(SAVE_DIR, filename)
                cv2.imwrite(path, frame)
                print(f"Saved {path}")

            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        cap.release()


if __name__ == "__main__":
    main()
